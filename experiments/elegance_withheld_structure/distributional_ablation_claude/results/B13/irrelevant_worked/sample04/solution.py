"""recipebook.py -- adapt recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Neither function mutates its inputs.  Standard library only.
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as authored, byte for byte."""
    return recipe["authored_text"]


def _best_choice(name, catalog):
    """Lowest numeric priority wins; catalog order breaks ties."""
    best = None
    best_key = None
    for index, choice in enumerate(catalog):
        if choice["for"] == name:
            key = (choice["priority"], index)
            if best_key is None or key < best_key:
                best, best_key = choice, key
    return best


def _get_list(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if value:
            return value
    return []


def _render(recipe):
    lines = [recipe["title"], "Yield: %s" % recipe["yield"], "", "Ingredients:"]
    for ing in recipe["ingredients"]:
        part = "- %s %s %s" % (ing["quantity"], ing.get("unit") or "", ing["name"])
        if ing.get("preparation"):
            part += ", %s" % ing["preparation"]
        lines.append(" ".join(part.split()))
    if recipe.get("equipment"):
        lines.append("")
        lines.append("Equipment: %s" % ", ".join(recipe["equipment"]))
    lines.append("")
    lines.append("Instructions:")
    for step in recipe["instructions"]:
        lines.append(step)
    for comp in recipe.get("components", []):
        lines.append("")
        lines.append("Component: %s" % comp.get("name", ""))
        for ing in comp.get("ingredients", []):
            part = "- %s %s %s" % (ing["quantity"], ing.get("unit") or "", ing["name"])
            if ing.get("preparation"):
                part += ", %s" % ing["preparation"]
            lines.append(" ".join(part.split()))
        for step in comp.get("instructions", []):
            lines.append(step)
    return "\n".join(lines)


def adapt(recipe, request, catalog):
    target = Fraction(request["target_yield"])
    available = set(request["available_equipment"])
    excluded = set(request["excluded"])
    factor = target / Fraction(recipe["yield"])

    choices = []            # catalog ids in the order they were applied/considered
    warnings = set()
    reasons = set()
    wording_changes = []    # (old, new) pairs in application order
    equipment = list(recipe.get("equipment", []))
    extra_queue = []        # additional ingredients awaiting resolution
    possible = True

    def apply_result(ing, choice):
        result = choice["result"]
        old_name = ing["name"]
        ing["name"] = result["name"]
        if "quantity_factor" in result:
            ing["quantity"] = ing["quantity"] * Fraction(result["quantity_factor"])
        if "unit" in result and result["unit"] != ing.get("unit"):
            warnings.add("unit of %s changed from %s to %s"
                         % (old_name, ing.get("unit"), result["unit"]))
            ing["unit"] = result["unit"]
        if "preparation" in result:
            ing["preparation"] = result["preparation"]
        for pair in _get_list(result, "wording_changes"):
            wording_changes.append((pair["old"], pair["new"]))
        for item in _get_list(result, "add_equipment", "equipment_add", "equipment_additions"):
            if item not in equipment:
                equipment.append(item)
                warnings.add("equipment added: %s" % item)
        for item in _get_list(result, "remove_equipment", "equipment_remove", "equipment_removals"):
            if item in equipment:
                equipment.remove(item)
                warnings.add("equipment removed: %s" % item)
        for extra in _get_list(result, "additional_ingredients", "add_ingredients"):
            new_extra = copy.deepcopy(extra)
            if "quantity" in new_extra:
                new_extra["quantity"] = Fraction(new_extra["quantity"]) * factor
            extra_queue.append(new_extra)
        if "yield_factor" in result:
            warnings.add("yield affected by substitution %s" % choice["id"])

    def resolve(ing):
        """Follow the substitution chain until the name is allowed.

        Returns the adapted ingredient, or None (recording a reason) when the
        chain dead-ends or revisits a name.
        """
        seen = set()
        while ing["name"] in excluded:
            name = ing["name"]
            if name in seen:
                reasons.add("substitution cycle involving %s" % name)
                return None
            seen.add(name)
            choice = _best_choice(name, catalog)
            if choice is None:
                reasons.add("no substitution for %s" % name)
                return None
            choices.append(choice["id"])
            apply_result(ing, choice)
        return ing

    def process_ingredients(source, destination):
        nonlocal possible
        for ing in source:
            working = copy.deepcopy(ing)
            working["quantity"] = Fraction(working["quantity"]) * factor
            resolved = resolve(working)
            if resolved is None:
                possible = False
            else:
                destination.append(resolved)

    adapted = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": [],
        "equipment": [],
        "instructions": list(recipe.get("instructions", [])),
        "components": [],
    }

    process_ingredients(recipe.get("ingredients", []), adapted["ingredients"])
    for comp in recipe.get("components", []) or []:
        new_comp = copy.deepcopy(comp)
        if "ingredients" in comp:
            resolved_list = []
            process_ingredients(comp["ingredients"], resolved_list)
            new_comp["ingredients"] = resolved_list
        adapted["components"].append(new_comp)

    while extra_queue:
        resolved = resolve(extra_queue.pop(0))
        if resolved is None:
            possible = False
        else:
            adapted["ingredients"].append(resolved)

    def apply_wording(lines):
        out = []
        for line in lines:
            for old, new in wording_changes:
                line = line.replace(old, new)
            out.append(line)
        return out

    adapted["instructions"] = apply_wording(adapted["instructions"])
    for comp in adapted["components"]:
        if "instructions" in comp:
            comp["instructions"] = apply_wording(list(comp["instructions"]))

    for item in equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)
    adapted["equipment"] = sorted(equipment)

    if reasons:
        possible = False

    if not possible:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    adapted["authored_text"] = _render(adapted)
    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
