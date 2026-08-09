from copy import deepcopy
from fractions import Fraction


def _key(value):
    return str(value).casefold()


def _nodes(value):
    if not isinstance(value, dict):
        return
    yield value
    components = value.get("components", [])
    if isinstance(components, list):
        children = components
    elif isinstance(components, dict):
        children = components.values()
    else:
        children = ()
    for child in children:
        if isinstance(child, dict):
            yield from _nodes(child)


def _catalog_entries(catalog):
    if isinstance(catalog, dict):
        catalog = catalog.get("choices", [])
    return list(catalog or [])


def _targets(choice):
    target = choice.get("for")
    if isinstance(target, list):
        return {_key(item) for item in target}
    return {_key(target)}


def _choices_for(ingredient, catalog):
    names = {_key(ingredient.get("name", ""))}
    if ingredient.get("id") is not None:
        names.add(_key(ingredient["id"]))
    matches = []
    for index, choice in enumerate(catalog):
        if names & _targets(choice):
            matches.append((choice.get("priority", 0), index, choice))
    matches.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in matches]


def _as_strings(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _wording_pairs(value):
    pairs = []
    for change in value or []:
        if isinstance(change, dict) and "old" in change and "new" in change:
            pairs.append((str(change["old"]), str(change["new"])))
        elif isinstance(change, (list, tuple)) and len(change) == 2:
            pairs.append((str(change[0]), str(change[1])))
    return pairs


def _remove_equipment(recipe, removals):
    removal_keys = {_key(item) for item in removals}
    if not removal_keys:
        return
    for node in _nodes(recipe):
        equipment = node.get("equipment")
        if isinstance(equipment, list):
            node["equipment"] = [
                item for item in equipment if _key(item) not in removal_keys
            ]


def _add_equipment(recipe, additions):
    equipment = recipe.setdefault("equipment", [])
    present = {_key(item) for item in equipment}
    for item in additions:
        if _key(item) not in present:
            equipment.append(item)
            present.add(_key(item))


def _apply_wording(recipe, changes):
    for node in _nodes(recipe):
        instructions = node.get("instructions")
        if not isinstance(instructions, list):
            continue
        rewritten = []
        for instruction in instructions:
            text = instruction
            for old, new in changes:
                text = text.replace(old, new)
            rewritten.append(text)
        node["instructions"] = rewritten


def _scale_recipe(recipe, factor, target_yield):
    for node in _nodes(recipe):
        ingredients = node.get("ingredients")
        if isinstance(ingredients, list):
            for ingredient in ingredients:
                if isinstance(ingredient, dict) and "quantity" in ingredient:
                    ingredient["quantity"] = (
                        Fraction(ingredient["quantity"]) * factor
                    )
        if "yield" in node:
            node["yield"] = Fraction(node["yield"]) * factor
    recipe["yield"] = Fraction(target_yield)


def _sort_equipment(recipe):
    for node in _nodes(recipe):
        equipment = node.get("equipment")
        if isinstance(equipment, list):
            node["equipment"] = sorted(equipment, key=lambda item: str(item))


def adapt(recipe, request, catalog):
    adapted = deepcopy(recipe)
    entries = _catalog_entries(catalog)
    excluded = {_key(item) for item in request.get("excluded", [])}
    available = {_key(item) for item in request.get("available_equipment", [])}

    chosen_ids = []
    warnings = []
    reasons = set()
    wording_changes = []

    queue = []
    for node in _nodes(adapted):
        ingredients = node.get("ingredients")
        if isinstance(ingredients, list):
            for index, ingredient in enumerate(ingredients):
                if isinstance(ingredient, dict):
                    queue.append((ingredients, index, ()))

    cursor = 0
    while cursor < len(queue):
        ingredients, index, ancestry = queue[cursor]
        cursor += 1
        ingredient = ingredients[index]
        name = str(ingredient.get("name", ""))
        name_key = _key(name)

        if name_key not in excluded:
            continue
        if name_key in ancestry:
            reasons.add("substitution cycle involving " + name)
            continue

        candidates = _choices_for(ingredient, entries)
        if not candidates:
            reasons.add("no substitution for " + name)
            continue

        choice = candidates[0]
        chosen_ids.append(choice["id"])
        result = choice.get("result", {})

        warnings.extend(_as_strings(choice.get("warnings")))
        warnings.extend(_as_strings(choice.get("warning")))
        warnings.extend(_as_strings(result.get("warnings")))
        warnings.extend(_as_strings(result.get("warning")))

        replacement = deepcopy(ingredient)
        replacement["name"] = result.get("name", replacement.get("name"))
        if "quantity_factor" in result:
            replacement["quantity"] = (
                Fraction(replacement["quantity"])
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]
        ingredients[index] = replacement

        changes = _wording_pairs(result.get("wording_changes"))
        wording_changes.extend(changes)

        removals = result.get(
            "equipment_removals", result.get("remove_equipment", [])
        )
        additions = result.get(
            "equipment_additions", result.get("add_equipment", [])
        )
        _remove_equipment(adapted, _as_strings(removals))
        _add_equipment(adapted, _as_strings(additions))

        if "yield" in result:
            adapted["yield"] = Fraction(result["yield"])
        if "yield_factor" in result:
            adapted["yield"] = (
                Fraction(adapted["yield"]) * Fraction(result["yield_factor"])
            )

        next_ancestry = ancestry + (name_key,)
        if _key(replacement.get("name", "")) in excluded:
            queue.append((ingredients, index, next_ancestry))

        extra = result.get(
            "additional_ingredients", result.get("additional", [])
        )
        if isinstance(extra, dict):
            extra = [extra]
        for additional in extra or []:
            ingredients.append(deepcopy(additional))
            queue.append((ingredients, len(ingredients) - 1, next_ancestry))

    _apply_wording(adapted, wording_changes)

    current_yield = Fraction(adapted["yield"])
    target_yield = Fraction(request["target_yield"])
    _scale_recipe(adapted, target_yield / current_yield, target_yield)
    _sort_equipment(adapted)

    for node in _nodes(adapted):
        equipment = node.get("equipment")
        if isinstance(equipment, list):
            for item in equipment:
                if _key(item) not in available:
                    reasons.add("equipment " + str(item) + " unavailable")

    ordered_reasons = sorted(reasons)
    return {
        "possible": not ordered_reasons,
        "recipe": adapted if not ordered_reasons else None,
        "choices": chosen_ids,
        "warnings": sorted(set(warnings)),
        "reasons": ordered_reasons,
    }


def print_original(recipe):
    return recipe["authored_text"]
