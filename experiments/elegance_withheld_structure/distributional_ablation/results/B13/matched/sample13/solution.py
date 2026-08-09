"""Recipe adaptation with exact rational quantities."""

from copy import deepcopy
from fractions import Fraction


def _key(value):
    return str(value).strip().casefold()


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _ingredient_lists(recipe):
    lists = []
    for record in _walk_dicts(recipe):
        if isinstance(record.get("ingredients"), list):
            lists.append(record["ingredients"])
    return lists


def _replace_wording(recipe, old, new):
    if not old:
        return

    for record in _walk_dicts(recipe):
        instructions = record.get("instructions")
        if isinstance(instructions, list):
            record["instructions"] = [
                text.replace(old, new) if isinstance(text, str) else text
                for text in instructions
            ]

        ingredients = record.get("ingredients")
        if isinstance(ingredients, list):
            for ingredient in ingredients:
                preparation = ingredient.get("preparation")
                if isinstance(preparation, str):
                    ingredient["preparation"] = preparation.replace(old, new)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _change_equipment(recipe, removals, additions):
    removed = {_key(item) for item in removals}

    for record in _walk_dicts(recipe):
        equipment = record.get("equipment")
        if isinstance(equipment, list):
            record["equipment"] = [
                item for item in equipment if _key(item) not in removed
            ]

    equipment = recipe.setdefault("equipment", [])
    present = {_key(item) for item in equipment}
    for item in additions:
        if _key(item) not in present:
            equipment.append(item)
            present.add(_key(item))


def _equipment_changes(result):
    nested = result.get("equipment")
    nested = nested if isinstance(nested, dict) else {}

    removals = result.get(
        "equipment_removals",
        result.get("remove_equipment", nested.get("removals", nested.get("remove", []))),
    )
    additions = result.get(
        "equipment_additions",
        result.get("add_equipment", nested.get("additions", nested.get("add", []))),
    )
    return _as_list(removals), _as_list(additions)


def _all_equipment(recipe):
    values = []
    for record in _walk_dicts(recipe):
        if isinstance(record.get("equipment"), list):
            values.extend(record["equipment"])
    return values


def _sort_equipment(recipe):
    for record in _walk_dicts(recipe):
        if isinstance(record.get("equipment"), list):
            record["equipment"] = sorted(
                record["equipment"], key=lambda item: str(item)
            )


def _scale_quantities(recipe, factor):
    for ingredients in _ingredient_lists(recipe):
        for ingredient in ingredients:
            if "quantity" in ingredient:
                ingredient["quantity"] = (
                    Fraction(ingredient["quantity"]) * factor
                )


def adapt(recipe, request, catalog):
    """Return a complete adaptation result without mutating any input."""
    work = deepcopy(recipe)
    excluded = {_key(item) for item in request.get("excluded", [])}
    available = {
        _key(item) for item in request.get("available_equipment", [])
    }

    ordered_catalog = [
        choice
        for _, choice in sorted(
            enumerate(catalog),
            key=lambda pair: (pair[1].get("priority", 0), pair[0]),
        )
    ]

    choices = []
    warnings = []
    reasons = set()
    effective_yield = Fraction(work["yield"])

    def select(ingredient):
        name_key = _key(ingredient.get("name", ""))
        ingredient_id = ingredient.get("id")
        for choice in ordered_catalog:
            target = choice.get("for")
            if _key(target) == name_key or target == ingredient_id:
                return choice
        return None

    def resolve(container, ingredient, ancestry=()):
        nonlocal effective_yield

        name = ingredient.get("name", "")
        name_key = _key(name)
        if name_key not in excluded:
            return

        if name_key in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            return

        choice = select(ingredient)
        if choice is None:
            reasons.add(f"no substitution for {name}")
            return

        result = deepcopy(choice.get("result") or {})
        choices.append(choice.get("id"))

        replacement_name = result.get("name", name)
        replacement_key = _key(replacement_name)
        if replacement_key in ancestry + (name_key,):
            reasons.add(
                f"substitution cycle involving {replacement_name}"
            )
            return

        ingredient["name"] = replacement_name
        if "quantity_factor" in result:
            ingredient["quantity"] = (
                Fraction(ingredient.get("quantity", 0))
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        for change in _as_list(result.get("wording_changes")):
            if isinstance(change, dict):
                _replace_wording(
                    work,
                    str(change.get("old", "")),
                    str(change.get("new", "")),
                )

        removals, additions = _equipment_changes(result)
        _change_equipment(work, removals, additions)

        if "yield" in result:
            effective_yield = Fraction(result["yield"])
        elif "yield_factor" in result:
            effective_yield *= Fraction(result["yield_factor"])

        result_warnings = result.get(
            "warnings", result.get("warning", [])
        )
        warnings.extend(str(item) for item in _as_list(result_warnings))

        next_ancestry = ancestry + (name_key,)
        resolve(container, ingredient, next_ancestry)

        addition_ancestry = next_ancestry + (replacement_key,)
        for addition in _as_list(result.get("additional_ingredients")):
            if not isinstance(addition, dict):
                continue
            added = deepcopy(addition)
            container.append(added)
            resolve(container, added, addition_ancestry)

    # Snapshot each initial list because substitutions may append ingredients.
    for ingredients in _ingredient_lists(work):
        for ingredient in list(ingredients):
            resolve(ingredients, ingredient)

    for equipment in _all_equipment(work):
        if _key(equipment) not in available:
            reasons.add(f"equipment {equipment} unavailable")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    target_yield = Fraction(request["target_yield"])
    _scale_quantities(work, target_yield / effective_yield)
    work["yield"] = target_yield
    _sort_equipment(work)

    return {
        "possible": True,
        "recipe": work,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }


def print_original(recipe):
    """Return the authored recipe text byte-for-byte as a string."""
    return recipe["authored_text"]
