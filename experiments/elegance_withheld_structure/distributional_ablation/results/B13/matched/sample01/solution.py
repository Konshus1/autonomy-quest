"""Recipe adaptation with exact quantities and deterministic substitutions."""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return authored_text byte-for-byte without modifying the recipe."""
    return recipe["authored_text"]


def _same(left, right):
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and left.casefold() == right.casefold()
    )


def _component_values(section):
    components = section.get("components", ())
    if isinstance(components, dict):
        return components.values()
    if isinstance(components, (list, tuple)):
        return components
    return ()


def _sections(section):
    if not isinstance(section, dict):
        return
    yield section
    for component in _component_values(section):
        yield from _sections(component)


def _ingredient_lists(recipe):
    for section in _sections(recipe):
        ingredients = section.get("ingredients")
        if isinstance(ingredients, list):
            yield ingredients


def _catalog_entries(catalog):
    if isinstance(catalog, dict):
        catalog = catalog.get("choices", catalog.get("substitutions", ()))
    return list(catalog)


def _is_excluded(name, exclusions):
    return any(_same(name, excluded) for excluded in exclusions)


def _matching_choice(entries, ingredient):
    name = ingredient.get("name")
    identifier = ingredient.get("id")
    matches = []

    for index, choice in enumerate(entries):
        target = choice.get("for")
        targets = target if isinstance(target, (list, tuple, set)) else (target,)
        if any(
            _same(candidate, name) or _same(candidate, identifier)
            for candidate in targets
        ):
            matches.append((choice["priority"], index, choice))

    if not matches:
        return None
    return min(matches, key=lambda item: (item[0], item[1]))[2]


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _equipment_changes(result, action):
    if action == "add":
        keys = (
            "equipment_additions",
            "equipment_add",
            "add_equipment",
            "equipment_added",
        )
        nested_keys = ("additions", "add")
    else:
        keys = (
            "equipment_removals",
            "equipment_remove",
            "remove_equipment",
            "equipment_removed",
        )
        nested_keys = ("removals", "remove")

    for key in keys:
        if key in result:
            return _as_list(result[key])

    for container_key in ("equipment_changes", "equipment"):
        container = result.get(container_key)
        if isinstance(container, dict):
            for key in nested_keys:
                if key in container:
                    return _as_list(container[key])

    return []


def _apply_equipment(recipe, result):
    removals = _equipment_changes(result, "remove")
    additions = _equipment_changes(result, "add")

    for section in _sections(recipe):
        equipment = section.get("equipment")
        if isinstance(equipment, list):
            equipment[:] = [
                item
                for item in equipment
                if not any(_same(item, removal) for removal in removals)
            ]

    root_equipment = recipe.setdefault("equipment", [])
    for addition in additions:
        if not any(_same(addition, item) for item in root_equipment):
            root_equipment.append(addition)


def _apply_wording(recipe, changes):
    for change in changes or ():
        old = change["old"]
        new = change["new"]

        for section in _sections(recipe):
            instructions = section.get("instructions")
            if isinstance(instructions, list):
                instructions[:] = [
                    instruction.replace(old, new)
                    for instruction in instructions
                ]

        for ingredients in _ingredient_lists(recipe):
            for ingredient in ingredients:
                preparation = ingredient.get("preparation")
                if isinstance(preparation, str):
                    ingredient["preparation"] = preparation.replace(old, new)


def _updated_yield(current_yield, result):
    if "yield_factor" in result:
        return current_yield * Fraction(result["yield_factor"])
    if "yield_change" in result:
        return current_yield * Fraction(result["yield_change"])
    if "yield" in result:
        return Fraction(result["yield"])
    return current_yield


def adapt(recipe, request, catalog):
    """Return a deterministic adaptation result without mutating its inputs."""
    adapted = deepcopy(recipe)
    entries = _catalog_entries(catalog)
    exclusions = tuple(request.get("excluded", ()))
    available = tuple(request.get("available_equipment", ()))

    choices = []
    warnings = set()
    reasons = set()
    effective_yield = Fraction(adapted["yield"])

    # Queue items contain the ingredient's owning list, the ingredient itself,
    # and the replacement ancestry used to identify substitution cycles.
    queue = []
    for ingredients in _ingredient_lists(adapted):
        queue.extend(
            (ingredients, ingredient, ())
            for ingredient in list(ingredients)
        )

    cursor = 0
    while cursor < len(queue):
        container, ingredient, ancestry = queue[cursor]
        cursor += 1
        name = ingredient.get("name", "")

        if not _is_excluded(name, exclusions):
            continue

        if any(_same(name, previous) for previous in ancestry):
            reasons.add("substitution cycle involving " + name)
            continue

        choice = _matching_choice(entries, ingredient)
        if choice is None:
            reasons.add("no substitution for " + name)
            continue

        choices.append(choice["id"])
        result = choice.get("result", {})
        next_ancestry = ancestry + (name,)

        ingredient["quantity"] = (
            Fraction(ingredient["quantity"])
            * Fraction(result.get("quantity_factor", 1))
        )
        ingredient["name"] = result.get("name", ingredient["name"])

        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        _apply_equipment(adapted, result)
        _apply_wording(adapted, result.get("wording_changes"))
        effective_yield = _updated_yield(effective_yield, result)

        # Recheck the replacement because it may also be excluded.
        queue.append((container, ingredient, next_ancestry))

        additions = deepcopy(result.get("additional_ingredients", ()))
        for addition in additions:
            container.append(addition)
            queue.append((container, addition, next_ancestry))

    target_yield = Fraction(request["target_yield"])
    scale = target_yield / effective_yield

    for ingredients in _ingredient_lists(adapted):
        for ingredient in ingredients:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    adapted["yield"] = target_yield

    for section in _sections(adapted):
        equipment = section.get("equipment")
        if not isinstance(equipment, list):
            continue

        equipment.sort()
        for item in equipment:
            if not any(_same(item, available_item) for available_item in available):
                reasons.add("equipment " + item + " unavailable")

    return {
        "possible": not reasons,
        "recipe": None if reasons else adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }
