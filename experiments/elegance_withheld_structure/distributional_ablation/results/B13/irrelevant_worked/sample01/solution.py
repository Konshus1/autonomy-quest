# recipebook.py

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as authored."""
    return recipe["authored_text"]


def _children(container):
    components = container.get("components") or []
    if isinstance(components, dict):
        components = components.values()
    return (item for item in components if isinstance(item, dict))


def _containers(recipe):
    if not isinstance(recipe, dict):
        return
    yield recipe
    for component in _children(recipe):
        yield from _containers(component)


def _catalog_index(catalog):
    if isinstance(catalog, dict):
        choices = catalog.get("choices", [])
    else:
        choices = catalog or []

    indexed = {}
    for position, choice in enumerate(choices):
        key = choice.get("for")
        if isinstance(key, dict):
            key = key.get("name")
        indexed.setdefault(key, []).append(
            (choice.get("priority", 0), position, choice)
        )

    for entries in indexed.values():
        entries.sort(key=lambda entry: (entry[0], entry[1]))
    return indexed


def _additional_ingredients(result):
    additions = result.get(
        "additional_ingredients",
        result.get("ingredients", []),
    )
    if not additions:
        return []
    if isinstance(additions, dict):
        additions = [additions]
    return deepcopy(list(additions))


def _collect_warnings(source, warnings):
    warning = source.get("warning")
    if warning:
        if isinstance(warning, list):
            warnings.update(warning)
        else:
            warnings.add(warning)

    extra = source.get("warnings") or []
    if isinstance(extra, str):
        warnings.add(extra)
    else:
        warnings.update(extra)


def adapt(recipe, request, catalog):
    """Return a complete, non-mutating adaptation result."""
    work = deepcopy(recipe)
    excluded = set(request.get("excluded") or [])
    available = set(request.get("available_equipment") or [])
    substitutions = _catalog_index(catalog)

    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    applied_results = []

    equipment = set()
    for container in _containers(work):
        equipment.update(container.get("equipment") or [])

    # Entries are (owning ingredient list, ingredient, substitution ancestry).
    queue = []
    for container in _containers(work):
        ingredients = container.get("ingredients")
        if isinstance(ingredients, list):
            for ingredient in ingredients:
                queue.append(
                    (ingredients, ingredient, (ingredient.get("name"),))
                )

    cursor = 0
    while cursor < len(queue):
        owner, ingredient, ancestry = queue[cursor]
        cursor += 1
        name = ingredient.get("name")

        if name not in excluded:
            continue

        candidates = substitutions.get(name, [])
        if not candidates:
            reasons.add("no substitution for %s" % name)
            continue

        choice = candidates[0][2]
        result = choice.get("result") or {}
        replacement = result.get("name", name)
        additions = _additional_ingredients(result)
        introduced = [replacement]
        introduced.extend(item.get("name") for item in additions)

        repeated = next(
            (
                introduced_name
                for introduced_name in introduced
                if introduced_name in excluded
                and introduced_name in ancestry
            ),
            None,
        )
        if repeated is not None:
            choices.append(choice.get("id"))
            reasons.add("substitution cycle involving %s" % repeated)
            continue

        choices.append(choice.get("id"))
        applied_results.append(result)
        _collect_warnings(choice, warnings)
        _collect_warnings(result, warnings)

        ingredient["name"] = replacement
        if "quantity_factor" in result:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"])
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        wording_changes.extend(result.get("wording_changes") or [])

        removals = result.get(
            "equipment_removals",
            result.get("remove_equipment", []),
        ) or []
        additions_equipment = result.get(
            "equipment_additions",
            result.get("add_equipment", []),
        ) or []
        equipment.difference_update(removals)
        equipment.update(additions_equipment)

        for extra in additions:
            owner.append(extra)
            queue.append(
                (owner, extra, ancestry + (extra.get("name"),))
            )

        if replacement in excluded:
            queue.append(
                (owner, ingredient, ancestry + (replacement,))
            )

    # Apply instruction changes in the order their choices were applied.
    for container in _containers(work):
        instructions = container.get("instructions")
        if not isinstance(instructions, list):
            continue
        for index, text in enumerate(instructions):
            for change in wording_changes:
                text = text.replace(change["old"], change["new"])
            instructions[index] = text

    # Catalogs may express a changed base yield as an absolute yield or factor.
    base_yield = Fraction(work["yield"])
    for result in applied_results:
        if "yield" in result:
            base_yield = Fraction(result["yield"])
        elif "yield_factor" in result:
            base_yield *= Fraction(result["yield_factor"])

    target_yield = Fraction(request["target_yield"])
    scale = target_yield / base_yield

    for container in _containers(work):
        ingredients = container.get("ingredients")
        if not isinstance(ingredients, list):
            continue
        for ingredient in ingredients:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"]) * scale
            )

    work["yield"] = target_yield

    for item in equipment - available:
        reasons.add("equipment %s unavailable" % item)

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    work["equipment"] = sorted(equipment)
    return {
        "possible": True,
        "recipe": work,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
