"""Recipe adaptation using only the Python standard library."""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the original authored text byte-for-byte."""
    return recipe["authored_text"]


def _key(value):
    return str(value).casefold()


def _component_nodes(recipe):
    """Yield the recipe and every recipe-shaped optional component."""
    yield recipe
    components = recipe.get("components", [])

    if isinstance(components, list):
        values = components
    elif isinstance(components, dict):
        if any(
            field in components
            for field in ("ingredients", "equipment", "instructions", "components")
        ):
            values = [components]
        else:
            values = list(components.values())
    else:
        values = []

    for component in values:
        if isinstance(component, dict):
            yield from _component_nodes(component)


def _catalog_entries(catalog):
    if isinstance(catalog, dict):
        catalog = catalog.get("choices", catalog.get("substitutions", []))
    return list(catalog or [])


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _equipment_change(result, primary, alias):
    return _as_list(result.get(primary, result.get(alias, [])))


def _wording_changes(result):
    for change in result.get("wording_changes", []) or []:
        if isinstance(change, dict):
            yield str(change["old"]), str(change["new"])
        else:
            old, new = change
            yield str(old), str(new)


def _result_warnings(result):
    warnings = result.get("warnings", [])
    if isinstance(warnings, str):
        yield warnings
    else:
        yield from warnings or []

    if "warning" in result:
        yield str(result["warning"])


def adapt(recipe, request, catalog):
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    nodes = list(_component_nodes(adapted))
    entries = _catalog_entries(catalog)

    excluded = {_key(name) for name in request.get("excluded", [])}
    available = {
        _key(name) for name in request.get("available_equipment", [])
    }

    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    equipment_additions = []
    equipment_removals = set()
    effective_yield = Fraction(adapted["yield"])

    # Each queue entry is (owning ingredient list, ingredient, ancestry).
    # Ancestry contains normalized/display-name pairs for cycle reporting.
    queue = []
    for node in nodes:
        ingredients = node.get("ingredients", [])
        if not isinstance(ingredients, list):
            continue
        for ingredient in ingredients:
            name = str(ingredient.get("name", ingredient.get("id", "")))
            queue.append((ingredients, ingredient, ((_key(name), name),)))

    cursor = 0
    while cursor < len(queue):
        owner, ingredient, ancestry = queue[cursor]
        cursor += 1

        name = str(ingredient.get("name", ingredient.get("id", "")))
        if _key(name) not in excluded:
            continue

        matching_keys = {_key(name)}
        if "id" in ingredient:
            matching_keys.add(_key(ingredient["id"]))

        candidates = []
        for catalog_index, choice in enumerate(entries):
            if _key(choice.get("for", "")) in matching_keys:
                candidates.append(
                    (choice.get("priority", 0), catalog_index, choice)
                )

        if not candidates:
            reasons.add("no substitution for " + name)
            continue

        _, _, choice = min(candidates, key=lambda item: (item[0], item[1]))
        choices.append(choice["id"])

        result = choice.get("result", {}) or {}
        replacement = result.get("ingredient", result)
        if not isinstance(replacement, dict):
            replacement = {}

        if "quantity_factor" in replacement:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"])
                * Fraction(replacement["quantity_factor"])
            )

        for field in ("id", "name", "unit", "preparation"):
            if field in replacement:
                ingredient[field] = deepcopy(replacement[field])

        wording_changes.extend(_wording_changes(result))
        equipment_additions.extend(
            _equipment_change(
                result, "equipment_additions", "add_equipment"
            )
        )
        equipment_removals.update(
            _key(item)
            for item in _equipment_change(
                result, "equipment_removals", "remove_equipment"
            )
        )
        warnings.update(str(item) for item in _result_warnings(result))

        if "yield_factor" in result:
            effective_yield *= Fraction(result["yield_factor"])
        elif "yield" in result:
            effective_yield = Fraction(result["yield"])

        previous_keys = {key for key, _ in ancestry}
        new_name = str(ingredient.get("name", ingredient.get("id", "")))
        new_key = _key(new_name)

        if new_key in excluded:
            if new_key in previous_keys:
                repeated = next(
                    display for key, display in ancestry if key == new_key
                )
                reasons.add("substitution cycle involving " + repeated)
            else:
                queue.append(
                    (owner, ingredient, ancestry + ((new_key, new_name),))
                )

        additional = result.get("additional_ingredients", []) or []
        if isinstance(additional, dict):
            additional = [additional]

        for source in additional:
            extra = deepcopy(source)
            owner.append(extra)
            extra_name = str(extra.get("name", extra.get("id", "")))
            extra_key = _key(extra_name)

            if extra_key in excluded and extra_key in previous_keys:
                repeated = next(
                    display for key, display in ancestry if key == extra_key
                )
                reasons.add("substitution cycle involving " + repeated)
            else:
                queue.append(
                    (owner, extra, ancestry + ((extra_key, extra_name),))
                )

    # Apply selected text rewrites in selection order to every component.
    for node in nodes:
        instructions = node.get("instructions", [])
        if not isinstance(instructions, list):
            continue
        for index, instruction in enumerate(instructions):
            rewritten = instruction
            for old, new in wording_changes:
                rewritten = rewritten.replace(old, new)
            instructions[index] = rewritten

    # Substitution equipment changes apply to the complete recipe.
    if not isinstance(adapted.get("equipment"), list):
        adapted["equipment"] = []
    adapted["equipment"].extend(deepcopy(equipment_additions))

    for node in nodes:
        equipment = node.get("equipment")
        if not isinstance(equipment, list):
            continue

        unique = {}
        for item in equipment:
            if _key(item) not in equipment_removals:
                unique.setdefault(_key(item), item)
        equipment[:] = sorted(unique.values())

        for item in equipment:
            if _key(item) not in available:
                reasons.add("equipment " + str(item) + " unavailable")

    if effective_yield == 0:
        raise ZeroDivisionError("recipe yield cannot be zero")

    target_yield = Fraction(request["target_yield"])
    scale = target_yield / effective_yield

    # Recompute nodes because additional ingredients may have been appended.
    for node in _component_nodes(adapted):
        ingredients = node.get("ingredients", [])
        if not isinstance(ingredients, list):
            continue
        for ingredient in ingredients:
            if "quantity" in ingredient:
                ingredient["quantity"] = (
                    Fraction(ingredient["quantity"]) * scale
                )

    adapted["yield"] = target_yield
    final_reasons = sorted(reasons)

    return {
        "possible": not final_reasons,
        "recipe": adapted if not final_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": final_reasons,
    }


__all__ = ["adapt", "print_original"]
