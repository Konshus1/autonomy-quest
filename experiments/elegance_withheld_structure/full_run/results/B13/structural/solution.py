"""Recipe adaptation with exact rational quantities."""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as originally authored."""
    return recipe["authored_text"]


def _normalized(values):
    return {str(value).casefold() for value in values}


def _components(container):
    components = container.get("components", [])
    if isinstance(components, dict):
        components = components.values()
    return [component for component in components if isinstance(component, dict)]


def _containers(recipe):
    yield recipe
    for component in _components(recipe):
        yield from _containers(component)


def _all_ingredients(recipe):
    for container in _containers(recipe):
        for ingredient in container.get("ingredients", []):
            yield ingredient


def _catalog_index(catalog):
    indexed = {}
    for position, choice in enumerate(catalog):
        key = str(choice["for"]).casefold()
        indexed.setdefault(key, []).append((position, choice))

    for candidates in indexed.values():
        candidates.sort(key=lambda item: (item[1]["priority"], item[0]))
    return indexed


def _replacement(result):
    nested = result.get("ingredient")
    return nested if isinstance(nested, dict) else result


def _additional_ingredients(result):
    additions = result.get("additional_ingredients", [])
    return additions if additions is not None else []


def _wording_changes(result):
    changes = result.get("wording_changes", [])
    return changes if changes is not None else []


def _apply_wording(recipe, changes):
    for container in _containers(recipe):
        if "instructions" not in container:
            continue

        rewritten = []
        for statement in container["instructions"]:
            for change in changes:
                statement = statement.replace(
                    str(change["old"]), str(change["new"])
                )
            rewritten.append(statement)
        container["instructions"] = rewritten


def _equipment_changes(result):
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

    return additions or [], removals or []


def _apply_equipment_changes(recipe, result):
    additions, removals = _equipment_changes(result)
    current = set(recipe.get("equipment", []))
    current.difference_update(removals)
    current.update(additions)
    recipe["equipment"] = sorted(current)


def _apply_replacement(recipe, ingredient, destination, result):
    replacement = _replacement(result)
    ingredient["name"] = replacement["name"]
    ingredient["quantity"] = Fraction(ingredient["quantity"]) * Fraction(
        replacement.get("quantity_factor", 1)
    )

    if "unit" in replacement:
        ingredient["unit"] = replacement["unit"]
    if "preparation" in replacement:
        ingredient["preparation"] = replacement["preparation"]

    added = []
    for specification in _additional_ingredients(result):
        extra = deepcopy(specification)
        extra["quantity"] = Fraction(extra["quantity"])
        if "quantity_factor" in extra:
            extra["quantity"] *= Fraction(extra.pop("quantity_factor"))
        destination.append(extra)
        added.append(extra)

    _apply_wording(recipe, _wording_changes(result))
    _apply_equipment_changes(recipe, result)
    return added


def _updated_yield(current_yield, result):
    replacement = _replacement(result)

    if "yield_factor" in result:
        return current_yield * Fraction(result["yield_factor"])
    if replacement is not result and "yield_factor" in replacement:
        return current_yield * Fraction(replacement["yield_factor"])
    if "yield" in result:
        return Fraction(result["yield"])
    if replacement is not result and "yield" in replacement:
        return Fraction(replacement["yield"])
    return current_yield


def _collect_warnings(choice, result, warnings):
    for source in (choice, result):
        if "warning" in source:
            warnings.add(str(source["warning"]))
        for warning in source.get("warnings", []) or []:
            warnings.add(str(warning))


def _required_equipment(recipe):
    required = set()
    for container in _containers(recipe):
        required.update(container.get("equipment", []))
    return required


def adapt(recipe, request, catalog):
    """Return a constraint-checked adaptation without mutating any input."""
    work = deepcopy(recipe)
    effective_yield = Fraction(work["yield"])
    target_yield = Fraction(request["target_yield"])
    excluded = _normalized(request.get("excluded", []))
    available = _normalized(request.get("available_equipment", []))
    indexed_catalog = _catalog_index(catalog)

    choices = []
    warnings = set()
    reasons = set()

    # Queue entries contain the owning ingredient list, the ingredient, and the
    # replacement ancestry used to recognize cycles.
    queue = []
    for container in _containers(work):
        destination = container.get("ingredients", [])
        for ingredient in list(destination):
            queue.append((destination, ingredient, ()))

    cursor = 0
    while cursor < len(queue):
        destination, ingredient, ancestry = queue[cursor]
        cursor += 1

        name = str(ingredient["name"])
        key = name.casefold()
        if key not in excluded:
            continue

        if key in ancestry:
            reasons.add("substitution cycle involving " + name)
            continue

        candidates = indexed_catalog.get(key, [])
        if not candidates:
            reasons.add("no substitution for " + name)
            continue

        _, choice = candidates[0]
        result = choice["result"]
        choices.append(choice["id"])
        _collect_warnings(choice, result, warnings)

        added = _apply_replacement(
            work, ingredient, destination, result
        )
        effective_yield = _updated_yield(effective_yield, result)
        next_ancestry = ancestry + (key,)

        # Recheck the replacement itself and every introduced ingredient.
        queue.append((destination, ingredient, next_ancestry))
        for extra in added:
            queue.append((destination, extra, next_ancestry))

    required_equipment = _required_equipment(work)
    work["equipment"] = sorted(required_equipment)
    for equipment in required_equipment:
        if equipment.casefold() not in available:
            reasons.add("equipment " + equipment + " unavailable")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    scale = target_yield / effective_yield
    for ingredient in _all_ingredients(work):
        ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale
    work["yield"] = target_yield

    return {
        "possible": True,
        "recipe": work,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
