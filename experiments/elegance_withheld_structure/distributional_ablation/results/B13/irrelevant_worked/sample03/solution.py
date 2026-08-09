"""Recipe adaptation using only the Python standard library."""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the original authored recipe byte-for-byte."""
    return recipe["authored_text"]


def _key(value):
    return str(value).casefold()


def _list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _ingredient_lists(node):
    if isinstance(node, dict):
        if isinstance(node.get("ingredients"), list):
            yield node["ingredients"]
        yield from _ingredient_lists(node.get("components", []))
    elif isinstance(node, list):
        for child in node:
            yield from _ingredient_lists(child)


def _instruction_lists(node):
    if isinstance(node, dict):
        if isinstance(node.get("instructions"), list):
            yield node["instructions"]
        yield from _instruction_lists(node.get("components", []))
    elif isinstance(node, list):
        for child in node:
            yield from _instruction_lists(child)


def _equipment_lists(node):
    if isinstance(node, dict):
        if isinstance(node.get("equipment"), list):
            yield node["equipment"]
        yield from _equipment_lists(node.get("components", []))
    elif isinstance(node, list):
        for child in node:
            yield from _equipment_lists(child)


def _catalog_index(catalog):
    index = {}
    for order, choice in enumerate(catalog):
        targets = choice.get("for")
        targets = targets if isinstance(targets, (list, tuple)) else [targets]
        for target in targets:
            index.setdefault(_key(target), []).append(
                (choice.get("priority", 0), order, choice)
            )
    for target, entries in index.items():
        entries.sort(key=lambda entry: (entry[0], entry[1]))
        index[target] = [entry[2] for entry in entries]
    return index


def _equipment_delta(result):
    additions = []
    removals = []

    for key in ("equipment_additions", "equipment_add", "add_equipment"):
        additions.extend(_list(result.get(key)))
    for key in ("equipment_removals", "equipment_remove", "remove_equipment"):
        removals.extend(_list(result.get(key)))

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions.extend(_list(equipment.get("add")))
        removals.extend(_list(equipment.get("remove")))

    return additions, removals


def adapt(recipe, request, catalog):
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    work = deepcopy(recipe)
    catalog_index = _catalog_index(catalog)
    excluded = {_key(name) for name in request.get("excluded", [])}
    available = {_key(name) for name in request.get("available_equipment", [])}

    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    equipment_additions = []
    equipment_removals = []
    yield_factor = Fraction(1)
    replacement_yield = None

    def candidates_for(ingredient):
        candidates = catalog_index.get(_key(ingredient.get("name", "")), [])
        if not candidates and ingredient.get("id") is not None:
            candidates = catalog_index.get(_key(ingredient["id"]), [])
        return candidates

    def resolve(ingredient, ancestry=()):
        nonlocal yield_factor, replacement_yield

        name = str(ingredient.get("name", ""))
        name_key = _key(name)
        if name_key not in excluded:
            return [ingredient]

        if name_key in ancestry:
            reasons.add("substitution cycle involving " + name)
            return []

        candidates = candidates_for(ingredient)
        if not candidates:
            reasons.add("no substitution for " + name)
            return []

        choice = candidates[0]
        if choice.get("id") is not None:
            choices.append(choice["id"])

        result = choice.get("result") or {}
        replacement = deepcopy(ingredient)

        if "name" in result:
            replacement["name"] = result["name"]
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        replacement["quantity"] *= Fraction(result.get("quantity_factor", 1))

        for change in _list(result.get("wording_changes")):
            if isinstance(change, dict) and "old" in change and "new" in change:
                wording_changes.append((str(change["old"]), str(change["new"])))

        additions, removals = _equipment_delta(result)
        equipment_additions.extend(additions)
        equipment_removals.extend(removals)

        warnings.update(str(item) for item in _list(result.get("warnings")))
        if result.get("warning") is not None:
            warnings.add(str(result["warning"]))

        if "yield_factor" in result:
            yield_factor *= Fraction(result["yield_factor"])
        if "yield" in result:
            replacement_yield = Fraction(result["yield"])

        next_ancestry = ancestry + (name_key,)
        resolved = resolve(replacement, next_ancestry)

        for additional in _list(result.get("additional_ingredients")):
            if isinstance(additional, dict):
                resolved.extend(resolve(deepcopy(additional), next_ancestry))

        return resolved

    # Resolve every authored ingredient list in stable order.
    for ingredients in list(_ingredient_lists(work)):
        original = list(ingredients)
        ingredients[:] = []
        for ingredient in original:
            ingredients.extend(resolve(ingredient))

    # Equipment removals apply throughout the complete recipe.
    removed = {_key(item) for item in equipment_removals}
    for equipment in _equipment_lists(work):
        equipment[:] = [item for item in equipment if _key(item) not in removed]

    root_equipment = work.setdefault("equipment", [])
    existing = {_key(item) for item in root_equipment}
    for item in equipment_additions:
        if _key(item) not in existing:
            root_equipment.append(item)
            existing.add(_key(item))

    # Apply wording changes in selected-choice order.
    for instructions in _instruction_lists(work):
        for index, statement in enumerate(instructions):
            for old, new in wording_changes:
                statement = statement.replace(old, new)
            instructions[index] = statement

    base_yield = (
        replacement_yield
        if replacement_yield is not None
        else Fraction(recipe["yield"])
    )
    base_yield *= yield_factor
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / base_yield

    for ingredients in _ingredient_lists(work):
        for ingredient in ingredients:
            ingredient["quantity"] *= scale

    work["yield"] = target_yield

    # Canonicalize and validate the union of all equipment requirements.
    equipment_by_key = {}
    for equipment in _equipment_lists(work):
        equipment[:] = sorted(equipment, key=str)
        for item in equipment:
            equipment_by_key.setdefault(_key(item), item)

    work["equipment"] = sorted(equipment_by_key.values(), key=str)
    for key, display_name in equipment_by_key.items():
        if key not in available:
            reasons.add("equipment " + str(display_name) + " unavailable")

    result = {
        "possible": not reasons,
        "recipe": work if not reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }
    return result
