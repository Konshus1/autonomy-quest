"""recipebook.py

Adapt authored recipes to a target yield, available equipment, dietary
exclusions, and a substitution catalog, without mutating the originals.
Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
"""

import copy
from fractions import Fraction

_MAX_STEPS = 1000  # guard against substitutions that endlessly introduce ingredients


def print_original(recipe):
    """Return the recipe exactly as authored, byte for byte."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])
    excluded = set(request.get("excluded", ()))
    available = set(request.get("available_equipment", ()))
    by_name = _index_catalog(catalog)

    choices = []
    warnings = set()
    reasons = set()
    wording = []
    equipment = set(recipe.get("equipment", ()))

    def merge(effects):
        choices.extend(effects["choices"])
        warnings.update(effects["warnings"])
        reasons.update(effects["reasons"])
        wording.extend(effects["wording"])
        equipment.update(effects["equip_add"])
        equipment.difference_update(effects["equip_remove"])

    main = _adapt_ingredients(recipe.get("ingredients", []), scale, excluded, by_name)
    merge(main)

    components = []
    for comp in recipe.get("components", []):
        effects = _adapt_ingredients(comp.get("ingredients", []), scale, excluded, by_name)
        if effects["reasons"] and comp.get("optional"):
            warnings.add("dropped optional component %s" % comp.get("name", "unnamed"))
            continue
        merge(effects)
        adapted_comp = copy.deepcopy(comp)
        adapted_comp["ingredients"] = effects["ingredients"]
        components.append(adapted_comp)

    # Equipment availability is checked independently of substitution failures
    # so every applicable reason is reported, not merely the first.
    for name in equipment - available:
        reasons.add("equipment %s unavailable" % name)

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    adapted = copy.deepcopy(recipe)
    adapted["yield"] = target
    adapted["ingredients"] = main["ingredients"]
    adapted["equipment"] = sorted(equipment)
    adapted["instructions"] = _rewrite(recipe.get("instructions", []), wording)
    for comp in components:
        comp["instructions"] = _rewrite(comp.get("instructions", []), wording)
    adapted["components"] = components
    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }


def _index_catalog(catalog):
    by_name = {}
    for position, choice in enumerate(catalog):
        by_name.setdefault(choice["for"], []).append(
            (choice.get("priority", 0), position, choice))
    for options in by_name.values():
        options.sort(key=lambda entry: (entry[0], entry[1]))
    return by_name


def _resolve_chain(name, excluded, by_name):
    """Follow catalog choices until the ingredient is no longer excluded.

    Returns (combined_effect, applied_choice_ids, failure_reason_or_None).
    A substitution may itself name an excluded ingredient, so the chain
    continues; revisiting an excluded name is a cycle.
    """
    combined = {
        "name": name,
        "factor": Fraction(1),
        "unit": None,
        "preparation": None,
        "wording": [],
        "equip_add": [],
        "equip_remove": [],
        "additional": [],
        "yield_notes": [],
    }
    applied = []
    visited = {name}
    current = name
    while current in excluded:
        options = by_name.get(current)
        if not options:
            return None, applied, "no substitution for %s" % current
        choice = options[0][2]
        applied.append(choice["id"])
        result = choice["result"]
        combined["name"] = result["name"]
        combined["factor"] *= Fraction(result.get("quantity_factor", 1))
        if "unit" in result:
            combined["unit"] = result["unit"]
        if "preparation" in result:
            combined["preparation"] = result["preparation"]
        combined["wording"].extend(result.get("wording_changes", ()))
        combined["equip_add"].extend(result.get("equipment_add", ()))
        combined["equip_remove"].extend(result.get("equipment_remove", ()))
        combined["additional"].extend(result.get("additional_ingredients", ()))
        if "yield_factor" in result:
            combined["yield_notes"].append(choice["id"])
        current = result["name"]
        if current in excluded and current in visited:
            return None, applied, "substitution cycle involving %s" % current
        visited.add(current)
    return combined, applied, None


def _adapt_ingredients(items, scale, excluded, by_name):
    effects = {
        "ingredients": [],
        "choices": [],
        "warnings": set(),
        "reasons": set(),
        "wording": [],
        "equip_add": [],
        "equip_remove": [],
    }
    # Additional ingredients introduced by substitutions join this queue so
    # they receive the same scaling, exclusion checks, and catalog choices.
    queue = [copy.deepcopy(item) for item in items]
    steps = 0
    while queue:
        item = queue.pop(0)
        steps += 1
        if steps > _MAX_STEPS:
            effects["reasons"].add(
                "substitution cycle involving %s" % item.get("name", "unknown"))
            break
        adapted = dict(item)
        adapted["quantity"] = Fraction(item.get("quantity", 1)) * scale
        name = item.get("name")
        if name in excluded:
            combined, chosen, error = _resolve_chain(name, excluded, by_name)
            effects["choices"].extend(chosen)
            if error is not None:
                effects["reasons"].add(error)
                continue
            adapted["name"] = combined["name"]
            adapted["quantity"] *= combined["factor"]
            if combined["unit"] is not None:
                if item.get("unit") != combined["unit"]:
                    effects["warnings"].add("unit changed for %s" % name)
                adapted["unit"] = combined["unit"]
            if combined["preparation"] is not None:
                adapted["preparation"] = combined["preparation"]
            effects["wording"].extend(combined["wording"])
            effects["equip_add"].extend(combined["equip_add"])
            effects["equip_remove"].extend(combined["equip_remove"])
            for eq in combined["equip_add"]:
                effects["warnings"].add("added equipment %s" % eq)
            for note in combined["yield_notes"]:
                effects["warnings"].add("yield affected by %s" % note)
            queue.extend(copy.deepcopy(extra) for extra in combined["additional"])
        effects["ingredients"].append(adapted)
    return effects


def _rewrite(lines, wording):
    rewritten = []
    for line in lines:
        for pair in wording:
            line = line.replace(pair["old"], pair["new"])
        rewritten.append(line)
    return rewritten
