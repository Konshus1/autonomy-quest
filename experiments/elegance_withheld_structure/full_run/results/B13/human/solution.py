from __future__ import annotations

import copy
import re
from collections import deque
from fractions import Fraction
from typing import Any, Iterable, Iterator


def print_original(recipe: dict[str, Any]) -> str:
    """Return the recipe's original authored text without modification."""
    return recipe["authored_text"]


def _normalized(value: Any) -> str:
    return str(value).strip().casefold()


def _component_children(node: dict[str, Any]) -> Iterable[dict[str, Any]]:
    components = node.get("components", [])
    if isinstance(components, list):
        return (item for item in components if isinstance(item, dict))
    if isinstance(components, dict):
        component_keys = {"ingredients", "equipment", "instructions", "components"}
        if component_keys.intersection(components):
            return (components,)
        return (item for item in components.values() if isinstance(item, dict))
    return ()


def _recipe_nodes(recipe: dict[str, Any]) -> Iterator[dict[str, Any]]:
    stack = [recipe]
    while stack:
        node = stack.pop()
        yield node
        children = list(_component_children(node))
        stack.extend(reversed(children))


def _ingredient_locations(
    recipe: dict[str, Any],
) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    for node in _recipe_nodes(recipe):
        ingredients = node.get("ingredients", [])
        if isinstance(ingredients, list):
            for ingredient in ingredients:
                if isinstance(ingredient, dict):
                    yield ingredient, ingredients


def _replace_ingredient_reference(text: str, old: str, new: str) -> str:
    if not old or old == new:
        return text

    pattern = re.compile(r"(?<!\w)" + re.escape(old) + r"(?!\w)", re.IGNORECASE)

    def replacement(match: re.Match[str]) -> str:
        found = match.group(0)
        if found and found[0].isupper() and new:
            return new[0].upper() + new[1:]
        return new

    return pattern.sub(replacement, text)


def _edit_text(
    recipe: dict[str, Any],
    old_name: str,
    new_name: str,
    wording_changes: Any,
) -> None:
    changes: list[tuple[str, str]] = []
    if isinstance(wording_changes, list):
        for change in wording_changes:
            if isinstance(change, dict) and "old" in change and "new" in change:
                changes.append((str(change["old"]), str(change["new"])))

    def edit(value: str) -> str:
        value = _replace_ingredient_reference(value, old_name, new_name)
        for old, new in changes:
            value = value.replace(old, new)
        return value

    for node in _recipe_nodes(recipe):
        instructions = node.get("instructions")
        if isinstance(instructions, list):
            node["instructions"] = [
                edit(value) if isinstance(value, str) else value
                for value in instructions
            ]

        ingredients = node.get("ingredients", [])
        if isinstance(ingredients, list):
            for ingredient in ingredients:
                if not isinstance(ingredient, dict):
                    continue
                preparation = ingredient.get("preparation")
                if isinstance(preparation, str):
                    ingredient["preparation"] = edit(preparation)


def _choice_targets(choice: dict[str, Any]) -> set[str]:
    target = choice.get("for")
    if isinstance(target, (list, tuple, set)):
        return {_normalized(item) for item in target}
    return {_normalized(target)}


def _matching_choice(
    choices: list[tuple[int, dict[str, Any]]], ingredient: dict[str, Any]
) -> dict[str, Any] | None:
    identities = {_normalized(ingredient.get("name", ""))}
    if "id" in ingredient:
        identities.add(_normalized(ingredient["id"]))

    matches = [
        (index, choice)
        for index, choice in choices
        if identities.intersection(_choice_targets(choice))
    ]
    if not matches:
        return None

    matches.sort(key=lambda item: (item[1].get("priority", 0), item[0]))
    return matches[0][1]


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            if isinstance(item, str):
                yield item


def _collect_warnings(choice: dict[str, Any], result: dict[str, Any]) -> set[str]:
    warnings: set[str] = set()
    for source in (choice, result):
        warnings.update(_strings(source.get("warning")))
        warnings.update(_strings(source.get("warnings")))
    return warnings


def _equipment_values(result: dict[str, Any], action: str) -> list[str]:
    keys = (
        ("equipment_additions", "add_equipment", "equipment_added")
        if action == "add"
        else ("equipment_removals", "remove_equipment", "equipment_removed")
    )
    values: list[str] = []
    for key in keys:
        values.extend(_strings(result.get(key)))
    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        values.extend(_strings(equipment.get(action)))
        values.extend(_strings(equipment.get(action + "itions")))
        if action == "remove":
            values.extend(_strings(equipment.get("removals")))
    return values


def _apply_equipment_changes(
    recipe: dict[str, Any], additions: list[str], removals: list[str]
) -> None:
    removed = {_normalized(item) for item in removals}
    for node in _recipe_nodes(recipe):
        equipment = node.get("equipment")
        if isinstance(equipment, list):
            node["equipment"] = [
                item for item in equipment if _normalized(item) not in removed
            ]

    top_level = recipe.setdefault("equipment", [])
    if not isinstance(top_level, list):
        top_level = []
        recipe["equipment"] = top_level

    existing = {_normalized(item) for item in top_level}
    for item in additions:
        key = _normalized(item)
        if key not in removed and key not in existing:
            top_level.append(item)
            existing.add(key)


def _sort_equipment(recipe: dict[str, Any]) -> None:
    for node in _recipe_nodes(recipe):
        equipment = node.get("equipment")
        if isinstance(equipment, list):
            unique: dict[str, str] = {}
            for item in equipment:
                if isinstance(item, str):
                    unique.setdefault(_normalized(item), item)
            node["equipment"] = sorted(unique.values())


def _scale_quantities(recipe: dict[str, Any], factor: Fraction) -> None:
    for ingredient, _ in _ingredient_locations(recipe):
        if "quantity" in ingredient:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * factor


def adapt(
    recipe: dict[str, Any], request: dict[str, Any], catalog: list[dict[str, Any]]
) -> dict[str, Any]:
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    working = copy.deepcopy(recipe)
    indexed_catalog = [(index, choice) for index, choice in enumerate(catalog)]
    excluded = {_normalized(item) for item in request.get("excluded", [])}
    available = {
        _normalized(item) for item in request.get("available_equipment", [])
    }

    selected: list[str] = []
    warnings: set[str] = set()
    reasons: set[str] = set()
    effective_yield = Fraction(recipe["yield"])

    queue: deque[
        tuple[dict[str, Any], list[dict[str, Any]], tuple[str, ...]]
    ] = deque()

    def enqueue(
        ingredient: dict[str, Any],
        container: list[dict[str, Any]],
        lineage: tuple[str, ...],
    ) -> None:
        name = str(ingredient.get("name", ""))
        key = _normalized(name)
        ingredient_id = _normalized(ingredient.get("id", ""))
        if key not in excluded and ingredient_id not in excluded:
            return
        if key in lineage:
            reasons.add(f"substitution cycle involving {name}")
            return
        queue.append((ingredient, container, lineage + (key,)))

    for ingredient, container in list(_ingredient_locations(working)):
        enqueue(ingredient, container, ())

    while queue:
        ingredient, container, lineage = queue.popleft()
        old_name = str(ingredient.get("name", ""))
        choice = _matching_choice(indexed_catalog, ingredient)
        if choice is None:
            reasons.add(f"no substitution for {old_name}")
            continue

        selected.append(str(choice.get("id", "")))
        raw_result = choice.get("result", {})
        result = raw_result if isinstance(raw_result, dict) else {}
        replacement = result.get("ingredient", result)
        if not isinstance(replacement, dict):
            replacement = result

        new_name = str(replacement.get("name", old_name))
        ingredient["name"] = new_name
        if "quantity_factor" in replacement:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * Fraction(
                replacement["quantity_factor"]
            )
        if "unit" in replacement:
            ingredient["unit"] = replacement["unit"]
        if "preparation" in replacement:
            ingredient["preparation"] = replacement["preparation"]

        _edit_text(working, old_name, new_name, result.get("wording_changes", []))
        additions = _equipment_values(result, "add")
        removals = _equipment_values(result, "remove")
        _apply_equipment_changes(working, additions, removals)
        warnings.update(_collect_warnings(choice, result))

        if "yield" in result:
            effective_yield = Fraction(result["yield"])
        elif "yield_factor" in result:
            effective_yield *= Fraction(result["yield_factor"])
        elif "yield_change" in result:
            effective_yield += Fraction(result["yield_change"])

        enqueue(ingredient, container, lineage)

        additional = result.get("additional_ingredients", [])
        if isinstance(additional, dict):
            additional = [additional]
        if isinstance(additional, list):
            for supplied in additional:
                if not isinstance(supplied, dict):
                    continue
                added = copy.deepcopy(supplied)
                container.append(added)
                enqueue(added, container, lineage)

    _sort_equipment(working)
    for node in _recipe_nodes(working):
        equipment = node.get("equipment", [])
        if not isinstance(equipment, list):
            continue
        for item in equipment:
            if _normalized(item) not in available:
                reasons.add(f"equipment {item} unavailable")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": selected,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    target_yield = Fraction(request["target_yield"])
    _scale_quantities(working, target_yield / effective_yield)
    working["yield"] = target_yield

    return {
        "possible": True,
        "recipe": working,
        "choices": selected,
        "warnings": sorted(warnings),
        "reasons": [],
    }
