"""recipebook.py -- adapt authored recipes to a target yield, available
equipment, and dietary exclusions using a prioritized substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction.  Catalog ``result`` objects may
carry equipment edits under ``add_equipment``/``remove_equipment`` (aliases
``equipment_add``/``equipment_remove`` and ``equipment_additions``/
``equipment_removals`` are accepted) and extra ingredients under
``additional_ingredients`` (alias ``add_ingredients``).
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the authored text exactly as written."""
    return recipe["authored_text"]


def _first(mapping, keys, default):
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def adapt(recipe, request, catalog):
    work = copy.deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    ratio = target_yield / Fraction(work["yield"])
    excluded = set(request.get("excluded", []))
    available = set(request.get("available_equipment", []))

    ranked = {}
    for index, choice in enumerate(catalog):
        ranked.setdefault(choice["for"], []).append((choice["priority"], index, choice))
    for options in ranked.values():
        options.sort(key=lambda entry: (entry[0], entry[1]))

    reasons = set()
    warnings = set()
    chosen_ids = []
    wording_pairs = []
    equipment = set(work.get("equipment", []))

    def record_choice(choice_id):
        if choice_id not in chosen_ids:
            chosen_ids.append(choice_id)

    def resolve(ingredient, chain):
        name = ingredient["name"]
        if name not in excluded:
            return [ingredient]
        if name in chain:
            reasons.add("substitution cycle involving %s" % name)
            return []
        options = ranked.get(name)
        if not options:
            reasons.add("no substitution for %s" % name)
            return []
        choice = options[0][2]
        record_choice(choice["id"])
        result = choice["result"]

        replacement = dict(ingredient)
        replacement["name"] = result["name"]
        if "quantity_factor" in result:
            replacement["quantity"] = (
                Fraction(replacement["quantity"]) * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            if result["unit"] != ingredient.get("unit"):
                warnings.add(
                    "unit changed for %s from %s to %s"
                    % (name, ingredient.get("unit"), result["unit"])
                )
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        for pair in result.get("wording_changes", []):
            wording_pairs.append((pair["old"], pair["new"]))
        for item in _first(
            result, ("add_equipment", "equipment_add", "equipment_additions"), []
        ):
            if item not in equipment:
                equipment.add(item)
                warnings.add("equipment added: %s" % item)
        for item in _first(
            result, ("remove_equipment", "equipment_remove", "equipment_removals"), []
        ):
            equipment.discard(item)

        resolved = resolve(replacement, chain | {name})
        for extra in _first(result, ("additional_ingredients", "add_ingredients"), []):
            added = copy.deepcopy(extra)
            added["quantity"] = Fraction(added.get("quantity", 1)) * ratio
            added.setdefault("id", added["name"])
            added.setdefault("unit", None)
            added.setdefault("preparation", None)
            resolved.extend(resolve(added, chain | {name}))
        return resolved

    def resolve_all(ingredients):
        out = []
        for ingredient in ingredients:
            scaled = dict(ingredient)
            scaled["quantity"] = Fraction(scaled["quantity"]) * ratio
            out.extend(resolve(scaled, frozenset()))
        return out

    final_ingredients = resolve_all(work.get("ingredients", []))
    final_components = []
    for component in work.get("components", []):
        adapted_component = dict(component)
        if isinstance(component, dict) and "ingredients" in component:
            adapted_component["ingredients"] = resolve_all(component["ingredients"])
        final_components.append(adapted_component)

    for item in equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": chosen_ids,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    def reword(text):
        for old, new in wording_pairs:
            text = text.replace(old, new)
        return text

    for component in final_components:
        if isinstance(component, dict) and "instructions" in component:
            component["instructions"] = [reword(line) for line in component["instructions"]]

    adapted = {
        "title": work["title"],
        "yield": target_yield,
        "ingredients": final_ingredients,
        "equipment": sorted(equipment),
        "instructions": [reword(line) for line in work.get("instructions", [])],
        "components": final_components,
        "authored_text": work["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": chosen_ids,
        "warnings": sorted(warnings),
        "reasons": [],
    }
