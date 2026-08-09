from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe's original authored representation unchanged."""
    return recipe["authored_text"]


def _key(value):
    return value.casefold() if isinstance(value, str) else value


def _collect_lists(value, field, output):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == field and isinstance(child, list):
                output.append(child)
            else:
                _collect_lists(child, field, output)
    elif isinstance(value, list):
        for child in value:
            _collect_lists(child, field, output)


def _result_equipment(result):
    additions = result.get("equipment_additions", result.get("add_equipment", ()))
    removals = result.get("equipment_removals", result.get("remove_equipment", ()))

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions = equipment.get("additions", equipment.get("add", additions))
        removals = equipment.get("removals", equipment.get("remove", removals))

    return list(additions or ()), list(removals or ())


def adapt(recipe, request, catalog):
    """Adapt a recipe without modifying the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    effective_yield = Fraction(adapted["yield"])
    excluded = {_key(name) for name in request.get("excluded", ())}
    available = {_key(name) for name in request.get("available_equipment", ())}

    catalog_by_ingredient = {}
    for position, choice in enumerate(catalog):
        catalog_by_ingredient.setdefault(_key(choice["for"]), []).append(
            (choice["priority"], position, choice)
        )
    for candidates in catalog_by_ingredient.values():
        candidates.sort(key=lambda item: (item[0], item[1]))

    ingredient_lists = []
    instruction_lists = []
    equipment_lists = []
    _collect_lists(adapted, "ingredients", ingredient_lists)
    _collect_lists(adapted, "instructions", instruction_lists)
    _collect_lists(adapted, "equipment", equipment_lists)

    root_equipment = adapted.setdefault("equipment", [])
    if root_equipment not in equipment_lists:
        equipment_lists.insert(0, root_equipment)

    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []

    def change_equipment(additions, removals):
        removal_keys = {_key(item) for item in removals}
        if removal_keys:
            for equipment_list in equipment_lists:
                equipment_list[:] = [
                    item for item in equipment_list if _key(item) not in removal_keys
                ]

        present = {_key(item) for item in root_equipment}
        for item in additions:
            if _key(item) not in present:
                root_equipment.append(deepcopy(item))
                present.add(_key(item))

    def resolve_ingredient(source, ancestry):
        nonlocal effective_yield
        ingredient = deepcopy(source)
        additions_to_return = []

        while _key(ingredient.get("name")) in excluded:
            name = ingredient.get("name")
            name_key = _key(name)

            if name_key in ancestry:
                reasons.add("substitution cycle involving " + str(name))
                return [ingredient]

            candidates = catalog_by_ingredient.get(name_key, ())
            if not candidates:
                reasons.add("no substitution for " + str(name))
                return [ingredient]

            choice = candidates[0][2]
            choices.append(choice["id"])
            result = choice["result"]
            next_ancestry = ancestry + (name_key,)

            ingredient["name"] = result["name"]
            if "quantity_factor" in result:
                ingredient["quantity"] = (
                    Fraction(ingredient["quantity"])
                    * Fraction(result["quantity_factor"])
                )
            if "unit" in result:
                ingredient["unit"] = deepcopy(result["unit"])
            if "preparation" in result:
                ingredient["preparation"] = deepcopy(result["preparation"])

            for change in result.get("wording_changes", ()):
                wording_changes.append((change["old"], change["new"]))

            equipment_additions, equipment_removals = _result_equipment(result)
            change_equipment(equipment_additions, equipment_removals)

            if "yield" in result:
                effective_yield = Fraction(result["yield"])
            if "yield_factor" in result:
                effective_yield *= Fraction(result["yield_factor"])

            for additional in result.get("additional_ingredients", ()):
                additions_to_return.extend(
                    resolve_ingredient(additional, next_ancestry)
                )

            ancestry = next_ancestry

        return [ingredient] + additions_to_return

    for ingredients in ingredient_lists:
        original_ingredients = list(ingredients)
        resolved = []
        for ingredient in original_ingredients:
            resolved.extend(resolve_ingredient(ingredient, ()))
        ingredients[:] = resolved

    for instructions in instruction_lists:
        for index, statement in enumerate(instructions):
            revised = statement
            for old, new in wording_changes:
                revised = revised.replace(old, new)
            instructions[index] = revised

    scale = target_yield / effective_yield
    final_ingredient_lists = []
    _collect_lists(adapted, "ingredients", final_ingredient_lists)
    for ingredients in final_ingredient_lists:
        for ingredient in ingredients:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale
            if _key(ingredient.get("name")) in excluded:
                name = ingredient.get("name")
                name_key = _key(name)
                if name_key not in catalog_by_ingredient:
                    reasons.add("no substitution for " + str(name))

    adapted["yield"] = target_yield

    final_equipment_lists = []
    _collect_lists(adapted, "equipment", final_equipment_lists)
    for equipment in final_equipment_lists:
        equipment[:] = sorted(set(equipment))
        for item in equipment:
            if _key(item) not in available:
                reasons.add("equipment " + str(item) + " unavailable")

    sorted_reasons = sorted(reasons)
    return {
        "possible": not sorted_reasons,
        "recipe": adapted if not sorted_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted_reasons,
    }
