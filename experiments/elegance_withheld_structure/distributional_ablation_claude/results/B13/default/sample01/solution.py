"""recipebook.py -- adapt recipes to yield, equipment, and dietary constraints.

Quantities and yields are fractions.Fraction throughout; everything else is
plain JSON-shaped data.  Only the Python standard library is used.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe's authored text exactly as written."""
    return recipe["authored_text"]


def _select(name, catalog):
    """Pick the catalog choice for `name` deterministically:
    lowest numeric priority wins, catalog order breaks ties."""
    best = None
    best_key = None
    for index, choice in enumerate(catalog):
        if choice["for"] == name:
            key = (choice["priority"], index)
            if best_key is None or key < best_key:
                best, best_key = choice, key
    return best


def adapt(recipe, request, catalog):
    excluded = set(request.get("excluded") or [])
    available = set(request.get("available_equipment") or [])
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / Fraction(recipe["yield"])

    choices = []        # catalog ids in the order they were applied
    warnings = set()
    reasons = set()
    wordings = []       # (old, new) pairs, in application order
    equip_add = []
    equip_remove = set()

    def resolve(source):
        """Adapt one ingredient: scale it, then walk the substitution chain
        until its name is no longer excluded.  Returns the list of resulting
        ingredients (the replacement plus any recursively resolved additional
        ingredients) or None when the chain fails; failures are recorded in
        `reasons` but never abort processing of other items."""
        item = {
            "id": source.get("id", source["name"]),
            "name": source["name"],
            "quantity": Fraction(source.get("quantity", 0)) * scale,
            "unit": source.get("unit"),
            "preparation": source.get("preparation"),
        }
        seen = {item["name"]}
        extras = []
        failed = False
        while item["name"] in excluded:
            choice = _select(item["name"], catalog)
            if choice is None:
                reasons.add("no substitution for " + item["name"])
                failed = True
                break
            choices.append(choice["id"])
            result = choice["result"]
            item["quantity"] *= Fraction(result.get("quantity_factor", 1))
            if "unit" in result:
                item["unit"] = result["unit"]
            if "preparation" in result:
                item["preparation"] = result["preparation"]
            for pair in (result.get("wording_changes")
                         or choice.get("wording_changes") or []):
                wordings.append((pair["old"], pair["new"]))
            equip_add.extend(result.get("equipment_added") or [])
            equip_remove.update(result.get("equipment_removed") or [])
            extras.extend(result.get("additional_ingredients") or [])
            new_name = result["name"]
            if new_name in seen:
                reasons.add("substitution cycle involving " + new_name)
                failed = True
                break
            seen.add(new_name)
            item["name"] = new_name
        out = [] if failed else [item]
        for extra in extras:
            resolved = resolve(extra)
            if resolved is None:
                failed = True
            else:
                out.extend(resolved)
        return None if failed else out

    new_ingredients = []
    for ingredient in recipe.get("ingredients") or []:
        resolved = resolve(ingredient)
        if resolved is not None:
            new_ingredients.extend(resolved)

    new_components = []
    for component in recipe.get("components") or []:
        adapted_component = copy.deepcopy(component)
        comp_items = []
        for ingredient in component.get("ingredients") or []:
            resolved = resolve(ingredient)
            if resolved is not None:
                comp_items.extend(resolved)
        adapted_component["ingredients"] = comp_items
        if "equipment" in component:
            comp_equipment = sorted(set(component.get("equipment") or []))
            for name in comp_equipment:
                if name not in available:
                    reasons.add("equipment " + name + " unavailable")
            adapted_component["equipment"] = comp_equipment
        new_components.append(adapted_component)

    base_equipment = list(recipe.get("equipment") or [])
    for name in equip_remove:
        if name not in base_equipment:
            warnings.add("equipment " + name + " not present to remove")
    equipment = [e for e in base_equipment if e not in equip_remove]
    equipment.extend(e for e in equip_add if e not in equipment)
    equipment = sorted(set(equipment))
    for name in equipment:
        if name not in available:
            reasons.add("equipment " + name + " unavailable")

    # Warn about wording changes whose old text is absent at the point in the
    # sequence where they would apply (a later change may target text that an
    # earlier change introduced, so the check replays the sequence).
    probe = list(recipe.get("instructions") or [])
    for component in recipe.get("components") or []:
        probe.extend(component.get("instructions") or [])
    for old, new in wordings:
        if not any(old in line for line in probe):
            warnings.add("wording change %r not found" % old)
        probe = [line.replace(old, new) for line in probe]

    def rewrite(lines):
        out = []
        for line in lines:
            for old, new in wordings:
                line = line.replace(old, new)
            out.append(line)
        return out

    for component in new_components:
        if "instructions" in component:
            component["instructions"] = rewrite(component["instructions"])

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    adapted_recipe = {
        "title": recipe["title"],
        "yield": target_yield,
        "ingredients": new_ingredients,
        "equipment": equipment,
        "instructions": rewrite(recipe.get("instructions") or []),
        "components": new_components,
        "authored_text": recipe.get("authored_text", ""),
    }
    return {
        "possible": True,
        "recipe": adapted_recipe,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
