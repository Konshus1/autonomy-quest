"""recipebook.py -- adapt recipes to yield, equipment, and dietary exclusions.

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Public API: adapt(recipe, request, catalog) -> dict, print_original(recipe) -> str.
Neither function mutates its inputs.
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as authored, byte for byte."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    work = copy.deepcopy(recipe)
    target = Fraction(request["target_yield"])
    available = set(request["available_equipment"])
    excluded = set(request["excluded"])
    factor = target / Fraction(work["yield"])

    choices = []
    warnings = set()
    reasons = set()
    equipment = set(work.get("equipment", []))
    instructions = list(work.get("instructions", []))
    components = [dict(c) for c in work.get("components", [])]
    for comp in components:
        comp["instructions"] = list(comp.get("instructions", []))
    instruction_lists = [instructions] + [c["instructions"] for c in components]

    def apply_wording(pair):
        old, new = pair["old"], pair["new"]
        found = False
        for text_list in instruction_lists:
            for i, text in enumerate(text_list):
                if old in text:
                    text_list[i] = text.replace(old, new)
                    found = True
        if not found:
            warnings.add("wording '%s' not found" % old)

    def pick(name):
        best = None
        best_key = None
        for index, choice in enumerate(catalog):
            if choice["for"] != name:
                continue
            key = (choice["priority"], index)
            if best is None or key < best_key:
                best, best_key = choice, key
        return best

    def resolve(ingredient, seen):
        """Resolve one ingredient (and any chained additions) at target scale.

        Returns a list of final ingredients, or None if adaptation failed;
        failure reasons are accumulated rather than raised so that every
        independently applicable reason is reported.
        """
        current = dict(ingredient)
        current["quantity"] = Fraction(current["quantity"]) * factor
        visited = set(seen)
        visited.add(current["name"])
        produced = []
        while current["name"] in excluded:
            choice = pick(current["name"])
            if choice is None:
                reasons.add("no substitution for %s" % current["name"])
                return None
            choices.append(choice["id"])
            result = choice["result"]
            replacement = dict(current)
            replacement["name"] = result["name"]
            if "quantity_factor" in result:
                replacement["quantity"] = (
                    current["quantity"] * Fraction(result["quantity_factor"])
                )
            if "unit" in result:
                if result["unit"] != current.get("unit"):
                    warnings.add(
                        "unit of %s is %s" % (result["name"], result["unit"])
                    )
                replacement["unit"] = result["unit"]
            if "preparation" in result:
                replacement["preparation"] = result["preparation"]
            if replacement["name"] in visited:
                reasons.add(
                    "substitution cycle involving %s" % replacement["name"]
                )
                return None
            visited.add(replacement["name"])
            for pair in result.get("wording_changes", []):
                apply_wording(pair)
            for item in result.get("equipment_add", []):
                equipment.add(item)
            for item in result.get("equipment_remove", []):
                equipment.discard(item)
            if "yield_factor" in result:
                warnings.add("choice %s implies a yield adjustment" % choice["id"])
            for extra in result.get("additional_ingredients", []):
                sub = resolve(extra, visited)
                if sub is None:
                    return None
                produced.extend(sub)
            current = replacement
        return [current] + produced

    final_ingredients = []
    for ingredient in work.get("ingredients", []):
        resolved = resolve(ingredient, set())
        if resolved is not None:
            final_ingredients.extend(resolved)

    final_components = []
    for comp in components:
        snapshot = (
            set(reasons),
            len(choices),
            set(warnings),
            set(equipment),
            [list(lst) for lst in instruction_lists],
        )
        equipment.update(comp.get("equipment", []))
        missing = [e for e in comp.get("equipment", []) if e not in available]
        comp_ings = []
        failed = False
        for ingredient in comp.get("ingredients", []):
            resolved = resolve(ingredient, set())
            if resolved is None:
                failed = True
            else:
                comp_ings.extend(resolved)
        if comp.get("optional") and (failed or missing):
            saved_reasons, n_choices, saved_warn, saved_equip, saved_instr = snapshot
            reasons.clear()
            reasons.update(saved_reasons)
            del choices[n_choices:]
            warnings.clear()
            warnings.update(saved_warn)
            equipment.clear()
            equipment.update(saved_equip)
            for lst, saved in zip(instruction_lists, saved_instr):
                lst[:] = saved
            warnings.add(
                "omitted optional component %s" % comp.get("name", "unnamed")
            )
            continue
        new_comp = dict(comp)
        new_comp["ingredients"] = comp_ings
        new_comp["equipment"] = sorted(comp.get("equipment", []))
        final_components.append(new_comp)

    for item in equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    adapted = {
        "title": work["title"],
        "yield": target,
        "ingredients": final_ingredients,
        "equipment": sorted(equipment),
        "instructions": instructions,
        "components": final_components,
        "authored_text": work["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
