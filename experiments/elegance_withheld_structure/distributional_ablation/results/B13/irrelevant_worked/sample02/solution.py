from copy import deepcopy
from fractions import Fraction

__all__ = ["adapt", "print_original"]


def _normalize(value):
    return str(value).casefold()


def _ingredient_lists(node):
    result = []

    ingredients = node.get("ingredients")
    if isinstance(ingredients, list):
        result.append(ingredients)

    components = node.get("components", [])
    if isinstance(components, dict):
        components = [components]

    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                result.extend(_ingredient_lists(component))

    return result


def _equipment_changes(result):
    additions = []
    removals = []

    for key in ("equipment_additions", "equipment_add", "add_equipment"):
        values = result.get(key)
        if values:
            additions.extend(values)

    for key in ("equipment_removals", "equipment_remove", "remove_equipment"):
        values = result.get(key)
        if values:
            removals.extend(values)

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        for key in ("additions", "add"):
            values = equipment.get(key)
            if values:
                additions.extend(values)
        for key in ("removals", "remove"):
            values = equipment.get(key)
            if values:
                removals.extend(values)

    return additions, removals


def _edit_text(text, changes):
    for old, new in changes:
        if old:
            text = text.replace(old, new)
    return text


def _apply_wording(node, changes):
    instructions = node.get("instructions")
    if isinstance(instructions, list):
        node["instructions"] = [
            _edit_text(value, changes) if isinstance(value, str) else value
            for value in instructions
        ]

    components = node.get("components", [])
    if isinstance(components, dict):
        components = [components]

    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                _apply_wording(component, changes)


def adapt(recipe, request, catalog):
    adapted = deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    effective_yield = Fraction(adapted["yield"])

    if effective_yield == 0:
        raise ValueError("recipe yield must be nonzero")

    excluded = {_normalize(name) for name in request.get("excluded", [])}
    available = {
        _normalize(name) for name in request.get("available_equipment", [])
    }

    ordered_catalog = sorted(
        enumerate(catalog),
        key=lambda entry: (entry[1]["priority"], entry[0]),
    )
    choices_by_name = {}
    for _, choice in ordered_catalog:
        choices_by_name.setdefault(_normalize(choice["for"]), []).append(choice)

    chosen_ids = []
    reasons = set()
    warnings = set()
    wording_changes = []
    equipment = list(adapted.get("equipment", []))

    def apply_choice(choice):
        nonlocal effective_yield, equipment

        chosen_ids.append(choice["id"])
        result = choice["result"]

        for change in result.get("wording_changes", []):
            wording_changes.append((str(change["old"]), str(change["new"])))

        additions, removals = _equipment_changes(result)
        removed_names = {_normalize(name) for name in removals}
        equipment = [
            name for name in equipment if _normalize(name) not in removed_names
        ]
        equipment.extend(additions)

        if "yield_factor" in result:
            effective_yield *= Fraction(result["yield_factor"])
        if "recipe_yield_factor" in result:
            effective_yield *= Fraction(result["recipe_yield_factor"])
        if "yield_change" in result:
            effective_yield += Fraction(result["yield_change"])
        if "yield" in result:
            effective_yield = Fraction(result["yield"])

        return result

    for ingredients in _ingredient_lists(adapted):
        lineages = {}
        index = 0

        while index < len(ingredients):
            ingredient = ingredients[index]
            if not isinstance(ingredient, dict) or "name" not in ingredient:
                index += 1
                continue

            lineage = lineages.get(id(ingredient), ())
            additional = []

            while _normalize(ingredient["name"]) in excluded:
                name = str(ingredient["name"])
                normalized_name = _normalize(name)

                if normalized_name in lineage:
                    reasons.add(f"substitution cycle involving {name}")
                    break

                candidates = choices_by_name.get(normalized_name, [])
                if not candidates:
                    reasons.add(f"no substitution for {name}")
                    break

                result = apply_choice(candidates[0])
                lineage = lineage + (normalized_name,)

                ingredient["name"] = result["name"]

                if "quantity_factor" in result:
                    ingredient["quantity"] = (
                        Fraction(ingredient["quantity"])
                        * Fraction(result["quantity_factor"])
                    )
                if "unit" in result:
                    ingredient["unit"] = result["unit"]
                if "preparation" in result:
                    ingredient["preparation"] = result["preparation"]

                for extra in deepcopy(result.get("additional_ingredients", [])):
                    if isinstance(extra, dict):
                        additional.append(extra)
                        lineages[id(extra)] = lineage

            if additional:
                ingredients[index + 1:index + 1] = additional

            index += 1

    if effective_yield == 0:
        raise ValueError("adapted recipe yield must be nonzero")

    scale = target_yield / effective_yield
    for ingredients in _ingredient_lists(adapted):
        for ingredient in ingredients:
            if isinstance(ingredient, dict) and "quantity" in ingredient:
                ingredient["quantity"] = (
                    Fraction(ingredient["quantity"]) * scale
                )

    adapted["yield"] = target_yield
    adapted["equipment"] = sorted(set(equipment))
    _apply_wording(adapted, wording_changes)

    for name in adapted["equipment"]:
        if _normalize(name) not in available:
            reasons.add(f"equipment {name} unavailable")

    possible = not reasons
    return {
        "possible": possible,
        "recipe": adapted if possible else None,
        "choices": chosen_ids,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }


def print_original(recipe):
    return recipe["authored_text"]
