"""Recipe scaling and constraint-aware ingredient substitution."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterable, Mapping


def print_original(recipe: Mapping[str, Any]) -> str:
    """Return authored_text byte-for-byte."""
    return recipe["authored_text"]


def _key(value: Any) -> str:
    return str(value).casefold()


def _components(node: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    components = node.get("components", [])
    if isinstance(components, list):
        values = components
    elif isinstance(components, Mapping):
        values = components.values()
    else:
        values = ()
    for component in values:
        if isinstance(component, dict):
            yield component


def _nodes(recipe: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    if isinstance(recipe, dict):
        yield recipe
    for component in _components(recipe):
        yield from _nodes(component)


def _ingredient_lists(recipe: Mapping[str, Any]) -> Iterable[list[dict[str, Any]]]:
    ingredients = recipe.get("ingredients")
    if isinstance(ingredients, list):
        yield ingredients
    for component in _components(recipe):
        yield from _ingredient_lists(component)


def _catalog_index(
    catalog: Iterable[Mapping[str, Any]],
) -> dict[str, list[tuple[int, Mapping[str, Any]]]]:
    result: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for position, choice in enumerate(catalog):
        result.setdefault(_key(choice.get("for", "")), []).append(
            (position, choice)
        )
    for entries in result.values():
        entries.sort(key=lambda entry: (entry[1]["priority"], entry[0]))
    return result


def _select_choice(
    ingredient: Mapping[str, Any],
    catalog: Mapping[str, list[tuple[int, Mapping[str, Any]]]],
) -> Mapping[str, Any] | None:
    entries = catalog.get(_key(ingredient.get("name", "")))
    if not entries and "id" in ingredient:
        entries = catalog.get(_key(ingredient["id"]))
    return entries[0][1] if entries else None


def _equipment_changes(result: Mapping[str, Any]) -> tuple[list[Any], list[Any]]:
    additions = list(result.get("equipment_additions", []) or [])
    removals = list(result.get("equipment_removals", []) or [])

    additions.extend(result.get("add_equipment", []) or [])
    removals.extend(result.get("remove_equipment", []) or [])

    equipment = result.get("equipment")
    if isinstance(equipment, Mapping):
        additions.extend(
            equipment.get("additions", equipment.get("add", [])) or []
        )
        removals.extend(
            equipment.get("removals", equipment.get("remove", [])) or []
        )
    return additions, removals


def _warnings(choice: Mapping[str, Any], result: Mapping[str, Any]) -> Iterable[str]:
    for source in (choice, result):
        warning = source.get("warning")
        if warning is not None:
            yield str(warning)
        for item in source.get("warnings", []) or []:
            yield str(item)


def _rewrite_text(recipe: Mapping[str, Any], changes: list[tuple[str, str]]) -> None:
    for old, new in changes:
        for node in _nodes(recipe):
            instructions = node.get("instructions")
            if isinstance(instructions, list):
                node["instructions"] = [
                    str(instruction).replace(old, new)
                    for instruction in instructions
                ]

            ingredients = node.get("ingredients")
            if isinstance(ingredients, list):
                for ingredient in ingredients:
                    preparation = ingredient.get("preparation")
                    if isinstance(preparation, str):
                        ingredient["preparation"] = preparation.replace(old, new)


def adapt(
    recipe: Mapping[str, Any],
    request: Mapping[str, Any],
    catalog: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a constraint-aware adaptation without mutating any input."""
    adapted = deepcopy(dict(recipe))
    original_yield = Fraction(recipe["yield"])
    target_yield = Fraction(request["target_yield"])
    if original_yield == 0:
        raise ValueError("recipe yield must be nonzero")

    scale = target_yield / original_yield
    for ingredients in _ingredient_lists(adapted):
        for ingredient in ingredients:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale
    adapted["yield"] = target_yield

    exclusions = {_key(item) for item in request.get("excluded", [])}
    available = {
        _key(item) for item in request.get("available_equipment", [])
    }
    indexed_catalog = _catalog_index(catalog)

    choices: list[Any] = []
    warning_set: set[str] = set()
    reasons: set[str] = set()
    wording_changes: list[tuple[str, str]] = []

    equipment: dict[str, Any] = {}
    for node in _nodes(adapted):
        for item in node.get("equipment", []) or []:
            equipment[_key(item)] = item

    queue: list[
        tuple[list[dict[str, Any]], int, tuple[str, ...]]
    ] = []
    for owner in _ingredient_lists(adapted):
        queue.extend((owner, index, ()) for index in range(len(owner)))

    cursor = 0
    while cursor < len(queue):
        owner, index, ancestry = queue[cursor]
        cursor += 1
        ingredient = owner[index]
        name = str(ingredient.get("name", ""))
        name_key = _key(name)

        if name_key not in exclusions:
            continue
        if name_key in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            continue

        choice = _select_choice(ingredient, indexed_catalog)
        if choice is None:
            reasons.add(f"no substitution for {name}")
            continue

        choices.append(choice["id"])
        raw_result = choice.get("result", {}) or {}
        nested = raw_result.get("ingredient")
        result = dict(raw_result)
        if isinstance(nested, Mapping):
            result.update(nested)

        replacement = deepcopy(ingredient)
        replacement_name = str(result.get("name", name))
        replacement["name"] = replacement_name

        if "quantity_factor" in result:
            replacement["quantity"] = (
                Fraction(replacement["quantity"])
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]
        owner[index] = replacement

        wording_changes.append((name, replacement_name))
        for change in raw_result.get("wording_changes", []) or []:
            wording_changes.append((str(change["old"]), str(change["new"])))

        additions, removals = _equipment_changes(raw_result)
        for item in removals:
            equipment.pop(_key(item), None)
        for item in additions:
            equipment[_key(item)] = item

        warning_set.update(_warnings(choice, raw_result))
        next_ancestry = ancestry + (name_key,)
        queue.append((owner, index, next_ancestry))

        additional_ingredients = (
            raw_result.get("additional_ingredients", []) or []
        )
        for raw_ingredient in additional_ingredients:
            additional = deepcopy(dict(raw_ingredient))
            additional["quantity"] = (
                Fraction(additional["quantity"]) * scale
            )
            owner.append(additional)
            queue.append((owner, len(owner) - 1, next_ancestry))

    _rewrite_text(adapted, wording_changes)

    adapted["equipment"] = sorted(equipment.values())
    descendant_nodes = list(_nodes(adapted))[1:]
    for node in descendant_nodes:
        if "equipment" in node:
            node["equipment"] = []

    for key, display_name in equipment.items():
        if key not in available:
            reasons.add(f"equipment {display_name} unavailable")

    # Defensive final validation: a successful result cannot retain an excluded
    # ingredient even if a catalog entry was malformed.
    for ingredients in _ingredient_lists(adapted):
        for ingredient in ingredients:
            name = str(ingredient.get("name", ""))
            if _key(name) not in exclusions:
                continue
            cycle_reason = f"substitution cycle involving {name}"
            missing_reason = f"no substitution for {name}"
            if cycle_reason not in reasons and missing_reason not in reasons:
                reasons.add(missing_reason)

    sorted_reasons = sorted(reasons)
    return {
        "possible": not sorted_reasons,
        "recipe": adapted if not sorted_reasons else None,
        "choices": choices,
        "warnings": sorted(warning_set),
        "reasons": sorted_reasons,
    }


__all__ = ["adapt", "print_original"]
