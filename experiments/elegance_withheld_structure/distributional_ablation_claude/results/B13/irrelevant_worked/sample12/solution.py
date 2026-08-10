"""recipebook.py -- adapt authored recipes to yield, equipment, and dietary constraints.

Quantities and yields are fractions.Fraction throughout; no unit package is used.
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the authored text exactly as written."""
    return recipe["authored_text"]


def _index_catalog(catalog):
    """Map ingredient name -> choices ordered by (priority, catalog position)."""
    by_name = {}
    for position, choice in enumerate(catalog):
        by_name.setdefault(choice["for"], []).append(
            (choice.get("priority", 0), position, choice)
        )
    for options in by_name.values():
        options.sort(key=lambda entry: (entry[0], entry[1]))
    return by_name


def adapt(recipe, request, catalog):
    adapted = copy.deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    available = set(request.get("available_equipment") or [])
    excluded = set(request.get("excluded") or [])

    scale = target_yield / Fraction(adapted["yield"])
    by_name = _index_catalog(catalog)

    choices_made = []
    warnings = []
    reasons = set()
    wording_changes = []
    equip_add = set()
    equip_remove = set()
    yield_factor = Fraction(1)

    def resolve(ingredient, chain):
        """Rewrite one ingredient until it is not excluded, or record why it
        cannot be.  Returns (ingredient_or_None, additional_ingredient_work)."""
        nonlocal yield_factor
        additional = []
        while ingredient["name"] in excluded:
            name = ingredient["name"]
            if name in chain:
                reasons.add("substitution cycle involving %s" % name)
                return None, additional
            chain = chain + [name]
            options = by_name.get(name)
            if not options:
                reasons.add("no substitution for %s" % name)
                return None, additional
            choice = options[0][2]
            choices_made.append(choice["id"])
            result = choice["result"]

            if "quantity_factor" in result:
                ingredient["quantity"] = (
                    Fraction(ingredient["quantity"]) * Fraction(result["quantity_factor"])
                )
            if "unit" in result and result["unit"] != ingredient.get("unit"):
                warnings.append(
                    "unit of %s changed from %s to %s"
                    % (name, ingredient.get("unit"), result["unit"])
                )
                ingredient["unit"] = result["unit"]
            if "preparation" in result:
                ingredient["preparation"] = result["preparation"]
            if "yield_factor" in result:
                yield_factor *= Fraction(result["yield_factor"])
                warnings.append("yield changed by substitution %s" % choice["id"])
            ingredient["name"] = result["name"]

            for pair in result.get("wording_changes", []):
                wording_changes.append((pair["old"], pair["new"]))
            equip_add.update(result.get("add_equipment", result.get("equipment_add", [])))
            equip_remove.update(result.get("remove_equipment", result.get("equipment_remove", [])))

            for extra in result.get("additional_ingredients", []):
                extra = copy.deepcopy(extra)
                extra.setdefault("id", extra["name"])
                extra.setdefault("unit", "")
                extra.setdefault("preparation", "")
                extra["quantity"] = Fraction(extra.get("quantity", 1)) * scale
                additional.append((extra, list(chain)))
        return ingredient, additional

    def process_ingredients(ingredients):
        """Scale, then run every ingredient (and any substitution-introduced
        ones) through exclusion resolution.  Returns the surviving list."""
        pending = []
        for ingredient in ingredients:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale
            pending.append((ingredient, []))
        kept = []
        while pending:
            ingredient, chain = pending.pop(0)
            resolved, additional = resolve(ingredient, chain)
            if resolved is not None:
                kept.append(resolved)
            pending.extend(additional)
        return kept

    def process_section(section):
        if "ingredients" in section:
            section["ingredients"] = process_ingredients(section["ingredients"])
        if "equipment" in section:
            section["equipment"] = sorted(set(section["equipment"]))
            for item in section["equipment"]:
                if item not in available:
                    reasons.add("equipment %s unavailable" % item)
        for sub in section.get("components") or []:
            process_section(sub)

    adapted["ingredients"] = process_ingredients(adapted["ingredients"])
    for component in adapted.get("components") or []:
        process_section(component)

    final_equipment = sorted(
        (set(adapted.get("equipment") or []) | equip_add) - equip_remove
    )
    adapted["equipment"] = final_equipment
    for item in final_equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)

    def apply_wording(strings):
        rewritten = []
        for text in strings:
            for old, new in wording_changes:
                text = text.replace(old, new)
            rewritten.append(text)
        return rewritten

    adapted["instructions"] = apply_wording(adapted.get("instructions") or [])

    def rewrite_component_text(section):
        if "instructions" in section:
            section["instructions"] = apply_wording(section["instructions"])
        for sub in section.get("components") or []:
            rewrite_component_text(sub)

    for component in adapted.get("components") or []:
        rewrite_component_text(component)

    adapted["yield"] = target_yield * yield_factor

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices_made,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices_made,
        "warnings": sorted(warnings),
        "reasons": [],
    }
