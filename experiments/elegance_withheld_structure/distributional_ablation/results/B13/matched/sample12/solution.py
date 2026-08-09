"""Recipe adaptation using exact rational quantities."""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe's original authored text byte-for-byte."""
    return recipe["authored_text"]


def _key(value):
    return str(value).casefold()


def _ingredient_lists(value):
    if isinstance(value, dict):
        ingredients = value.get("ingredients")
        if isinstance(ingredients, list):
            yield ingredients
        components = value.get("components")
        if isinstance(components, (list, dict)):
            yield from _ingredient_lists(components)
    elif isinstance(value, list):
        for item in value:
            yield from _ingredient_lists(item)


def _instruction_lists(value):
    if isinstance(value, dict):
        instructions = value.get("instructions")
        if isinstance(instructions, list):
            yield instructions
        components = value.get("components")
        if isinstance(components, (list, dict)):
            yield from _instruction_lists(components)
    elif isinstance(value, list):
        for item in value:
            yield from _instruction_lists(item)


def _equipment_lists(value):
    if isinstance(value, dict):
        equipment = value.get("equipment")
        if isinstance(equipment, list):
            yield equipment
        components = value.get("components")
        if isinstance(components, (list, dict)):
            yield from _equipment_lists(components)
    elif isinstance(value, list):
        for item in value:
            yield from _equipment_lists(item)


def _equipment_edits(result):
    additions = result.get(
        "equipment_additions", result.get("add_equipment", [])
    )
    removals = result.get(
        "equipment_removals", result.get("remove_equipment", [])
    )

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions = equipment.get("additions", equipment.get("add", additions))
        removals = equipment.get("removals", equipment.get("remove", removals))

    return list(additions or []), list(removals or [])


def _catalog_index(catalog):
    indexed = {}
    for position, choice in enumerate(catalog):
        entry = (choice.get("priority", 0), position, choice)
        indexed.setdefault(_key(choice["for"]), []).append(entry)

    for candidates in indexed.values():
        candidates.sort(key=lambda item: (item[0], item[1]))

    return indexed


def adapt(recipe, request, catalog):
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    work = deepcopy(recipe)
    indexed_catalog = _catalog_index(catalog)
    excluded = {_key(name) for name in request.get("excluded", [])}

    choices = []
    warnings = []
    reasons = set()
    wording_changes = []

    root_equipment = work.setdefault("equipment", [])

    queue = []
    for ingredients in _ingredient_lists(work):
        for ingredient in list(ingredients):
            queue.append(
                (
                    ingredient,
                    ingredients,
                    [_key(ingredient.get("name", ""))],
                )
            )

    cursor = 0
    while cursor < len(queue):
        ingredient, owner, ancestry = queue[cursor]
        cursor += 1

        name = ingredient.get("name", "")
        name_key = _key(name)
        if name_key not in excluded:
            continue

        candidates = indexed_catalog.get(name_key)
        if not candidates and ingredient.get("id") is not None:
            candidates = indexed_catalog.get(_key(ingredient["id"]))

        if not candidates:
            reasons.add("no substitution for " + str(name))
            continue

        selected = candidates[0][2]
        result = selected.get("result", {})
        choices.append(selected["id"])

        replacement_name = result.get("name", name)
        replacement_key = _key(replacement_name)
        ingredient["name"] = replacement_name

        if "quantity_factor" in result:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"])
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        for change in result.get("wording_changes", []) or []:
            wording_changes.append((str(change["old"]), str(change["new"])))

        additions, removals = _equipment_edits(result)
        removal_keys = {_key(item) for item in removals}
        root_equipment[:] = [
            item for item in root_equipment if _key(item) not in removal_keys
        ]

        present = {_key(item) for item in root_equipment}
        for item in additions:
            if _key(item) not in present:
                root_equipment.append(item)
                present.add(_key(item))

        if "yield" in result:
            work["yield"] = Fraction(result["yield"])
        if "yield_factor" in result:
            work["yield"] = (
                Fraction(work["yield"]) * Fraction(result["yield_factor"])
            )

        if replacement_key in ancestry:
            reasons.add(
                "substitution cycle involving " + str(replacement_name)
            )
        else:
            queue.append(
                (ingredient, owner, ancestry + [replacement_key])
            )

        additional_ingredients = result.get("additional_ingredients", []) or []
        for additional in deepcopy(additional_ingredients):
            owner.append(additional)
            queue.append(
                (additional, owner, [_key(additional.get("name", ""))])
            )

    for instructions in _instruction_lists(work):
        for index, statement in enumerate(instructions):
            edited = statement
            for old, new in wording_changes:
                edited = edited.replace(old, new)
            instructions[index] = edited

    available = {
        _key(item) for item in request.get("available_equipment", [])
    }
    for equipment in _equipment_lists(work):
        unique = {}
        for item in equipment:
            unique.setdefault(_key(item), item)

        equipment[:] = sorted(
            unique.values(),
            key=lambda item: (str(item).casefold(), str(item)),
        )

        for item in equipment:
            if _key(item) not in available:
                reasons.add("equipment " + str(item) + " unavailable")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    target_yield = Fraction(request["target_yield"])
    current_yield = Fraction(work["yield"])
    scale = target_yield / current_yield

    for ingredients in _ingredient_lists(work):
        for ingredient in ingredients:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"]) * scale
            )

    work["yield"] = target_yield

    return {
        "possible": True,
        "recipe": work,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
