"""Dependency-free recipe adaptation."""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe's authored representation byte-for-byte."""
    return recipe["authored_text"]


def _key(value):
    return str(value).casefold()


def _ingredient_lists(value):
    if isinstance(value, dict):
        ingredients = value.get("ingredients")
        if isinstance(ingredients, list):
            yield ingredients
        for key, child in value.items():
            if key not in ("ingredients", "authored_text"):
                yield from _ingredient_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _ingredient_lists(child)


def _instruction_lists(value):
    if isinstance(value, dict):
        instructions = value.get("instructions")
        if isinstance(instructions, list):
            yield instructions
        for key, child in value.items():
            if key not in ("instructions", "authored_text"):
                yield from _instruction_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _instruction_lists(child)


def _names(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _result_names(result, *keys):
    for key in keys:
        if key in result:
            return _names(result[key])
    return []


def _find_choice(ingredient, ordered_catalog):
    candidates = {
        _key(ingredient.get("name", "")),
        _key(ingredient.get("id", "")),
    }
    for _, choice in ordered_catalog:
        targets = choice.get("for")
        if not isinstance(targets, list):
            targets = [targets]
        if any(_key(target) in candidates for target in targets):
            return choice
    return None


def _additional_ingredients(result):
    additions = result.get(
        "additional_ingredients", result.get("ingredients", [])
    )
    if isinstance(additions, dict):
        return [additions]
    return list(additions or [])


def adapt(recipe, request, catalog):
    adapted = deepcopy(recipe)
    original_yield = Fraction(recipe["yield"])
    target_yield = Fraction(request["target_yield"])
    excluded = {_key(name) for name in request.get("excluded", [])}
    available = {
        _key(name) for name in request.get("available_equipment", [])
    }

    ordered_catalog = sorted(
        enumerate(catalog),
        key=lambda item: (item[1].get("priority", 0), item[0]),
    )

    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    yield_factor = Fraction(1)
    equipment = set(adapted.get("equipment", []))

    # Entries contain the mutable ingredient, its container, and its ancestry.
    queue = []
    for ingredients in _ingredient_lists(adapted):
        for ingredient in ingredients:
            if isinstance(ingredient, dict):
                queue.append(
                    (
                        ingredient,
                        ingredients,
                        (_key(ingredient.get("name", "")),),
                    )
                )

    cursor = 0
    while cursor < len(queue):
        ingredient, container, lineage = queue[cursor]
        cursor += 1

        name = str(ingredient.get("name", ""))
        if _key(name) not in excluded:
            continue

        choice = _find_choice(ingredient, ordered_catalog)
        if choice is None:
            reasons.add("no substitution for " + name)
            continue

        choices.append(choice["id"])
        result = choice.get("result", {})
        replacement_name = str(result.get("name", name))
        replacement_key = _key(replacement_name)

        if replacement_key in lineage:
            reasons.add("substitution cycle involving " + replacement_name)
            continue

        ingredient["name"] = replacement_name
        if "quantity_factor" in result:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"])
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            ingredient["unit"] = deepcopy(result["unit"])
        if "preparation" in result:
            ingredient["preparation"] = deepcopy(result["preparation"])

        changes = result.get("wording_changes", [])
        if isinstance(changes, dict):
            changes = [changes]
        for change in changes:
            wording_changes.append(
                (str(change["old"]), str(change["new"]))
            )

        removals = _result_names(
            result,
            "equipment_removals",
            "equipment_removal",
            "equipment_remove",
        )
        additions = _result_names(
            result,
            "equipment_additions",
            "equipment_addition",
            "equipment_add",
        )
        equipment.difference_update(removals)
        equipment.update(additions)

        warnings.update(_names(choice.get("warnings")))
        warnings.update(
            _names(result.get("warnings", result.get("warning")))
        )

        if "yield_factor" in result:
            yield_factor *= Fraction(result["yield_factor"])
        elif "yield" in result:
            yield_factor *= Fraction(result["yield"]) / original_yield

        next_lineage = lineage + (replacement_key,)
        if replacement_key in excluded:
            queue.append((ingredient, container, next_lineage))

        for addition in _additional_ingredients(result):
            added = deepcopy(addition)
            container.append(added)
            added_key = _key(added.get("name", ""))
            queue.append(
                (added, container, next_lineage + (added_key,))
            )

    for instructions in _instruction_lists(adapted):
        for index, instruction in enumerate(instructions):
            final = instruction
            for old, new in wording_changes:
                final = final.replace(old, new)
            instructions[index] = final

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

    scale = target_yield / (original_yield * yield_factor)
    for ingredients in _ingredient_lists(adapted):
        for ingredient in ingredients:
            if isinstance(ingredient, dict) and "quantity" in ingredient:
                ingredient["quantity"] = (
                    Fraction(ingredient["quantity"]) * scale
                )

    adapted["yield"] = target_yield
    adapted["equipment"] = sorted(equipment)

    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
