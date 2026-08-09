from copy import deepcopy
from fractions import Fraction


def _normal(value):
    return str(value).strip().casefold()


def _unique_sorted(values):
    by_name = {}
    for value in values:
        key = _normal(value)
        if key not in by_name:
            by_name[key] = value
    return sorted(by_name.values(), key=lambda value: str(value))


def _ingredient_lists(value):
    if isinstance(value, dict):
        ingredients = value.get("ingredients")
        if isinstance(ingredients, list):
            yield ingredients
        for key, child in value.items():
            if key != "ingredients":
                yield from _ingredient_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _ingredient_lists(child)


def _equipment_lists(value):
    if isinstance(value, dict):
        equipment = value.get("equipment")
        if isinstance(equipment, list):
            yield equipment
        for key, child in value.items():
            if key != "equipment":
                yield from _equipment_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _equipment_lists(child)


def _edit_instructions(value, changes):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "instructions" and isinstance(child, list):
                edited = []
                for statement in child:
                    if isinstance(statement, str):
                        for old, new in changes:
                            statement = statement.replace(old, new)
                    edited.append(statement)
                value[key] = edited
            else:
                _edit_instructions(child, changes)
    elif isinstance(value, list):
        for child in value:
            _edit_instructions(child, changes)


def _choice_targets(choice):
    target = choice.get("for")
    if isinstance(target, (list, tuple, set)):
        return {_normal(item) for item in target}
    return {_normal(target)}


def _matching_choices(ingredient, indexed_catalog):
    keys = {_normal(ingredient.get("name", ""))}
    if ingredient.get("id") is not None:
        keys.add(_normal(ingredient["id"]))

    matches = []
    for index, choice in indexed_catalog:
        if keys & _choice_targets(choice):
            matches.append((choice.get("priority", 0), index, choice))
    matches.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in matches]


def _collect_warnings(choice, result, warnings):
    for source in (choice, result):
        warning = source.get("warning")
        if warning is not None:
            warnings.add(str(warning))
        supplied = source.get("warnings", [])
        if isinstance(supplied, str):
            warnings.add(supplied)
        else:
            for item in supplied:
                warnings.add(str(item))


def _wording_pairs(result):
    pairs = []
    for change in result.get("wording_changes", []):
        if isinstance(change, dict) and "old" in change and "new" in change:
            pairs.append((str(change["old"]), str(change["new"])))
        elif isinstance(change, (list, tuple)) and len(change) == 2:
            pairs.append((str(change[0]), str(change[1])))
    return pairs


def _equipment_values(result, primary, alias):
    values = result.get(primary, result.get(alias, []))
    if isinstance(values, str):
        return [values]
    return list(values)


def _apply_equipment_change(recipe, result):
    removals = {
        _normal(item)
        for item in _equipment_values(
            result, "equipment_removals", "remove_equipment"
        )
    }
    additions = _equipment_values(
        result, "equipment_additions", "add_equipment"
    )

    for equipment in _equipment_lists(recipe):
        equipment[:] = [item for item in equipment if _normal(item) not in removals]

    root_equipment = recipe.setdefault("equipment", [])
    root_equipment.extend(additions)


def _apply_yield_change(result, yield_state):
    if "yield_factor" in result:
        yield_state["value"] *= Fraction(result["yield_factor"])
    if "yield_change" in result:
        yield_state["value"] *= Fraction(result["yield_change"])
    if "yield" in result:
        change = result["yield"]
        if isinstance(change, dict):
            if "factor" in change:
                yield_state["value"] *= Fraction(change["factor"])
            elif "value" in change:
                yield_state["value"] = Fraction(change["value"])
        else:
            yield_state["value"] = Fraction(change)


def _additional_ingredients(result, replaced_quantity):
    additional = deepcopy(result.get("additional_ingredients", []))
    for ingredient in additional:
        if "quantity" not in ingredient and "quantity_factor" in ingredient:
            ingredient["quantity"] = (
                replaced_quantity * Fraction(ingredient.pop("quantity_factor"))
            )
    return additional


def adapt(recipe, request, catalog):
    working = deepcopy(recipe)
    excluded = {_normal(item) for item in request.get("excluded", [])}
    available = {_normal(item) for item in request.get("available_equipment", [])}
    indexed_catalog = list(enumerate(catalog))

    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    yield_state = {"value": Fraction(working["yield"])}

    def resolve(ingredient, active):
        name = str(ingredient.get("name", ""))
        name_key = _normal(name)
        if name_key not in excluded:
            return

        if name_key in active:
            reasons.add("substitution cycle involving " + name)
            return

        candidates = _matching_choices(ingredient, indexed_catalog)
        if not candidates:
            reasons.add("no substitution for " + name)
            return

        choice = candidates[0]
        result = choice.get("result", {})
        choices.append(choice["id"])
        _collect_warnings(choice, result, warnings)
        wording_changes.extend(_wording_pairs(result))
        _apply_equipment_change(working, result)
        _apply_yield_change(result, yield_state)

        old_quantity = Fraction(ingredient["quantity"])
        replacement = deepcopy(ingredient)
        replacement["name"] = result.get("name", replacement["name"])
        replacement["quantity"] = old_quantity * Fraction(
            result.get("quantity_factor", 1)
        )
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]
        ingredient.clear()
        ingredient.update(replacement)

        next_active = active + (name_key,)
        resolve(ingredient, next_active)
        return _additional_ingredients(result, old_quantity)

    # Process dynamically because substitutions can append further ingredients.
    for ingredients in list(_ingredient_lists(working)):
        position = 0
        while position < len(ingredients):
            added = resolve(ingredients[position], ())
            if added:
                ingredients[position + 1:position + 1] = added
            position += 1

    # Additional ingredients can themselves contain nested component structures.
    # Repeat for newly introduced ingredient lists not already exhausted.
    seen_lists = set()
    while True:
        pending = []
        for ingredients in _ingredient_lists(working):
            identity = id(ingredients)
            if identity not in seen_lists:
                seen_lists.add(identity)
                pending.append(ingredients)
        if not pending:
            break
        for ingredients in pending:
            position = 0
            while position < len(ingredients):
                added = resolve(ingredients[position], ())
                if added:
                    ingredients[position + 1:position + 1] = added
                position += 1

    _edit_instructions(working, wording_changes)

    effective_yield = yield_state["value"]
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / effective_yield
    for ingredients in _ingredient_lists(working):
        for ingredient in ingredients:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    working["yield"] = target_yield

    for equipment in _equipment_lists(working):
        equipment[:] = _unique_sorted(equipment)
        for item in equipment:
            if _normal(item) not in available:
                reasons.add("equipment " + str(item) + " unavailable")

    result = {
        "possible": not reasons,
        "recipe": working if not reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }
    return result


def print_original(recipe):
    return recipe["authored_text"]
