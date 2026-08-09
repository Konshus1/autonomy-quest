from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    return recipe["authored_text"]


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


def _next_excluded(recipe, excluded):
    for ingredients in _ingredient_lists(recipe):
        for index, ingredient in enumerate(ingredients):
            if not isinstance(ingredient, dict):
                continue
            if ingredient.get("_adapt_blocked"):
                continue
            if ingredient.get("name") in excluded:
                return ingredients, index, ingredient
    return None


def _priority(value):
    try:
        return Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return Fraction(0)


def _catalog_index(catalog):
    indexed = []
    for order, choice in enumerate(catalog):
        indexed.append((_priority(choice.get("priority", 0)), order, choice))
    indexed.sort(key=lambda item: (item[0], item[1]))
    return indexed


def _candidates(ingredient, indexed_catalog):
    name = ingredient.get("name")
    ingredient_id = ingredient.get("id")
    return [
        choice
        for _, _, choice in indexed_catalog
        if choice.get("for") == name or choice.get("for") == ingredient_id
    ]


def _change_instructions(value, changes):
    if isinstance(value, dict):
        instructions = value.get("instructions")
        if isinstance(instructions, list):
            changed = []
            for instruction in instructions:
                if not isinstance(instruction, str):
                    changed.append(instruction)
                    continue
                text = instruction
                for change in changes:
                    old = change.get("old")
                    new = change.get("new")
                    if isinstance(old, str) and old and isinstance(new, str):
                        text = text.replace(old, new)
                changed.append(text)
            value["instructions"] = changed
        for key, child in value.items():
            if key != "instructions":
                _change_instructions(child, changes)
    elif isinstance(value, list):
        for child in value:
            _change_instructions(child, changes)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _record_warnings(state, source):
    if not isinstance(source, dict):
        return
    for warning in _as_list(source.get("warnings")):
        state["warnings"].add(str(warning))
    if "warning" in source:
        for warning in _as_list(source.get("warning")):
            state["warnings"].add(str(warning))


def _equipment_effects(result):
    equipment = result.get("equipment", {})
    if not isinstance(equipment, dict):
        equipment = {}

    additions = result.get(
        "equipment_additions",
        result.get("add_equipment", equipment.get("add", [])),
    )
    removals = result.get(
        "equipment_removals",
        result.get("remove_equipment", equipment.get("remove", [])),
    )
    return _as_list(additions), _as_list(removals)


def _apply_choice(state, ingredients, index, ingredient, choice):
    result = deepcopy(choice.get("result") or {})
    replacement = deepcopy(ingredient)
    replacement.pop("_adapt_blocked", None)

    trail = list(ingredient.get("_adapt_trail", []))
    trail.append(ingredient.get("name"))
    replacement["_adapt_trail"] = trail

    if "name" in result:
        replacement["name"] = result["name"]
    if "unit" in result:
        replacement["unit"] = result["unit"]
    if "preparation" in result:
        replacement["preparation"] = result["preparation"]
    if "quantity_factor" in result:
        replacement["quantity"] = (
            Fraction(replacement["quantity"])
            * Fraction(result["quantity_factor"])
        )

    additional = deepcopy(result.get("additional_ingredients", []))
    if isinstance(additional, dict):
        additional = [additional]
    for extra in additional:
        if isinstance(extra, dict):
            extra["_adapt_trail"] = list(trail)

    ingredients[index] = replacement
    ingredients.extend(additional)

    changes = result.get("wording_changes", [])
    if isinstance(changes, dict):
        changes = [changes]
    _change_instructions(state["recipe"], changes)

    additions, removals = _equipment_effects(result)
    for item in removals:
        state["equipment"].discard(item)
    for item in additions:
        state["equipment"].add(item)

    if "yield" in result:
        state["base_yield"] = Fraction(result["yield"])
    if "yield_factor" in result:
        state["base_yield"] *= Fraction(result["yield_factor"])

    state["choices"].append(choice.get("id"))
    _record_warnings(state, choice)
    _record_warnings(state, result)


def _search(state, excluded, available, indexed_catalog):
    found = _next_excluded(state["recipe"], excluded)
    if found is None:
        for equipment in state["equipment"]:
            if equipment not in available:
                state["reasons"].add(
                    "equipment {} unavailable".format(equipment)
                )
        return (not state["reasons"], state)

    ingredients, index, ingredient = found
    name = ingredient.get("name")
    trail = ingredient.get("_adapt_trail", [])

    if name in trail:
        ingredient["_adapt_blocked"] = True
        state["reasons"].add(
            "substitution cycle involving {}".format(name)
        )
        return _search(state, excluded, available, indexed_catalog)

    candidates = _candidates(ingredient, indexed_catalog)
    if not candidates:
        ingredient["_adapt_blocked"] = True
        state["reasons"].add("no substitution for {}".format(name))
        return _search(state, excluded, available, indexed_catalog)

    first_failure = None
    for choice in candidates:
        branch = deepcopy(state)
        branch_ingredients, branch_index, branch_ingredient = _next_excluded(
            branch["recipe"], excluded
        )
        _apply_choice(
            branch,
            branch_ingredients,
            branch_index,
            branch_ingredient,
            choice,
        )
        possible, completed = _search(
            branch, excluded, available, indexed_catalog
        )
        if possible:
            return True, completed
        if first_failure is None:
            first_failure = completed

    return False, first_failure


def _strip_private(value):
    if isinstance(value, dict):
        for key in list(value):
            if key.startswith("_adapt_"):
                del value[key]
            else:
                _strip_private(value[key])
    elif isinstance(value, list):
        for child in value:
            _strip_private(child)


def _final_recipe(state, target_yield):
    recipe = deepcopy(state["recipe"])
    factor = Fraction(target_yield) / Fraction(state["base_yield"])

    for ingredients in _ingredient_lists(recipe):
        for ingredient in ingredients:
            if isinstance(ingredient, dict) and "quantity" in ingredient:
                ingredient["quantity"] = Fraction(ingredient["quantity"]) * factor

    recipe["yield"] = Fraction(target_yield)
    recipe["equipment"] = sorted(state["equipment"])
    _strip_private(recipe)
    return recipe


def adapt(recipe, request, catalog):
    copied = deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    excluded = set(request.get("excluded", []))
    available = set(request.get("available_equipment", []))

    state = {
        "recipe": copied,
        "base_yield": Fraction(copied["yield"]),
        "equipment": set(copied.get("equipment", [])),
        "choices": [],
        "warnings": set(),
        "reasons": set(),
    }

    possible, completed = _search(
        state,
        excluded,
        available,
        _catalog_index(catalog),
    )

    return {
        "possible": possible,
        "recipe": _final_recipe(completed, target_yield) if possible else None,
        "choices": completed["choices"],
        "warnings": sorted(completed["warnings"]),
        "reasons": [] if possible else sorted(completed["reasons"]),
    }
