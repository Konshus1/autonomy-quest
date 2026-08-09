"""Recipe adaptation using exact rational quantities."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterable


def print_original(recipe: dict) -> str:
    """Return the recipe exactly as authored."""
    return recipe["authored_text"]


def _key(value: Any) -> str:
    return str(value).casefold()


def _names(values: Iterable[Any]) -> set[str]:
    return {_key(value) for value in values}


def _ingredient_lists(value: Any) -> list[list[dict]]:
    found: list[list[dict]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "ingredients" and isinstance(child, list):
                found.append(child)
            elif key == "components":
                found.extend(_ingredient_lists(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_ingredient_lists(child))
    return found


def _instruction_lists(value: Any) -> list[list[str]]:
    found: list[list[str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "instructions" and isinstance(child, list):
                found.append(child)
            elif key == "components":
                found.extend(_instruction_lists(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_instruction_lists(child))
    return found


def _equipment_delta(result: dict, action: str) -> list[Any]:
    if action == "add":
        aliases = ("equipment_additions", "equipment_add", "add_equipment")
    else:
        aliases = ("equipment_removals", "equipment_remove", "remove_equipment")

    values: list[Any] = []
    for alias in aliases:
        values.extend(result.get(alias, []))

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        values.extend(equipment.get(action, []))
    return values


def _additional_ingredients(result: dict) -> list[dict]:
    return list(result.get("additional_ingredients", result.get("additional", [])))


def adapt(recipe: dict, request: dict, catalog: list[dict]) -> dict:
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    work = deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    effective_yield = Fraction(work["yield"])
    excluded = _names(request.get("excluded", []))
    available = _names(request.get("available_equipment", []))

    ranked: dict[str, list[tuple[Any, int, dict]]] = {}
    for index, choice in enumerate(catalog):
        ranked.setdefault(_key(choice["for"]), []).append(
            (choice.get("priority", 0), index, choice)
        )
    for candidates in ranked.values():
        candidates.sort(key=lambda item: (item[0], item[1]))

    equipment = list(work.get("equipment", []))
    wording_changes: list[tuple[str, str]] = []
    selected_choices: list[Any] = []
    failures: set[str] = set()

    for ingredients in _ingredient_lists(work):
        queue: list[tuple[dict, tuple[str, ...]]] = [
            (ingredient, ()) for ingredient in ingredients
        ]
        cursor = 0

        while cursor < len(queue):
            ingredient, ancestry = queue[cursor]
            cursor += 1

            name = str(ingredient["name"])
            normalized = _key(name)
            if normalized not in excluded:
                continue

            if normalized in ancestry:
                failures.add(f"substitution cycle involving {name}")
                continue

            candidates = ranked.get(normalized, [])
            if not candidates:
                failures.add(f"no substitution for {name}")
                continue

            choice = candidates[0][2]
            result = choice["result"]
            selected_choices.append(choice["id"])
            next_ancestry = ancestry + (normalized,)

            ingredient["name"] = result["name"]
            if "quantity_factor" in result:
                ingredient["quantity"] = (
                    Fraction(ingredient["quantity"])
                    * Fraction(result["quantity_factor"])
                )
            if "unit" in result:
                ingredient["unit"] = result["unit"]
            if "preparation" in result:
                ingredient["preparation"] = result["preparation"]

            for change in result.get("wording_changes", []):
                wording_changes.append((str(change["old"]), str(change["new"])))

            removals = _names(_equipment_delta(result, "remove"))
            if removals:
                equipment = [item for item in equipment if _key(item) not in removals]

            existing_equipment = _names(equipment)
            for item in _equipment_delta(result, "add"):
                if _key(item) not in existing_equipment:
                    equipment.append(item)
                    existing_equipment.add(_key(item))

            if "yield" in result:
                effective_yield = Fraction(result["yield"])
            if "yield_factor" in result:
                effective_yield *= Fraction(result["yield_factor"])

            additions = deepcopy(_additional_ingredients(result))
            ingredients.extend(additions)

            # Reconsider the replacement and all introduced ingredients because
            # any of them may themselves be excluded.
            queue.append((ingredient, next_ancestry))
            queue.extend((addition, next_ancestry) for addition in additions)

    for instructions in _instruction_lists(work):
        for index, statement in enumerate(instructions):
            for old, new in wording_changes:
                statement = statement.replace(old, new)
            instructions[index] = statement

    scale = target_yield / effective_yield
    for ingredients in _ingredient_lists(work):
        for ingredient in ingredients:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    work["yield"] = target_yield
    equipment.sort(key=str)
    work["equipment"] = equipment

    for item in equipment:
        if _key(item) not in available:
            failures.add(f"equipment {item} unavailable")

    reasons = sorted(failures)
    return {
        "possible": not reasons,
        "recipe": work if not reasons else None,
        "choices": selected_choices,
        "warnings": [],
        "reasons": reasons,
    }
