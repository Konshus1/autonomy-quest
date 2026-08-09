"""Adapt recipes using exact rational quantities and deterministic substitutions."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterable, Iterator


def print_original(recipe: dict) -> str:
    """Return the recipe's authored text unchanged."""
    return recipe["authored_text"]


def _name_set(values: Iterable[Any]) -> set[str]:
    return {
        value.get("name") if isinstance(value, dict) else value
        for value in values
    }


def _sections(section: Any) -> Iterator[dict]:
    if not isinstance(section, dict):
        return
    yield section
    for component in section.get("components", []) or []:
        yield from _sections(component)


def _scale_quantities(recipe: dict, factor: Fraction) -> None:
    for section in _sections(recipe):
        for ingredient in section.get("ingredients", []) or []:
            ingredient["quantity"] *= factor


def _catalog_candidates(ingredient: dict, catalog: list[dict]) -> list[dict]:
    keys = {ingredient["name"]}
    if "id" in ingredient:
        keys.add(ingredient["id"])

    candidates = [
        (choice.get("priority", 0), order, choice)
        for order, choice in enumerate(catalog)
        if choice.get("for") in keys
    ]
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [choice for _, _, choice in candidates]


def _equipment_changes(result: dict) -> tuple[set[str], set[str]]:
    additions = set(
        result.get("equipment_additions", [])
        or result.get("equipment_add", [])
        or result.get("add_equipment", [])
        or []
    )
    removals = set(
        result.get("equipment_removals", [])
        or result.get("equipment_remove", [])
        or result.get("remove_equipment", [])
        or []
    )

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions.update(
            equipment.get("additions", []) or equipment.get("add", []) or []
        )
        removals.update(
            equipment.get("removals", []) or equipment.get("remove", []) or []
        )

    return additions, removals


def _apply_wording(recipe: dict, changes: list[tuple[str, str]]) -> None:
    for section in _sections(recipe):
        instructions = section.get("instructions")
        if instructions is None:
            continue

        for index, instruction in enumerate(instructions):
            for old, new in changes:
                instruction = instruction.replace(old, new)
            instructions[index] = instruction


def adapt(recipe: dict, request: dict, catalog: list[dict]) -> dict:
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    target_yield = request["target_yield"]
    scale = target_yield / adapted["yield"]

    _scale_quantities(adapted, scale)
    adapted["yield"] = target_yield

    excluded = _name_set(request.get("excluded", []))
    available_equipment = _name_set(request.get("available_equipment", []))

    choices: list[str] = []
    warnings: set[str] = set()
    reasons: set[str] = set()
    wording_changes: list[tuple[str, str]] = []
    equipment = set(adapted.get("equipment", []) or [])

    def resolve(ingredient: dict, trail: tuple[str, ...]) -> list[dict]:
        name = ingredient["name"]
        if name not in excluded:
            return [ingredient]

        if name in trail:
            reasons.add(f"substitution cycle involving {name}")
            return []

        candidates = _catalog_candidates(ingredient, catalog)
        if not candidates:
            reasons.add(f"no substitution for {name}")
            return []

        choice = candidates[0]
        choices.append(choice["id"])
        result = choice["result"]

        replacement = deepcopy(ingredient)
        replacement["name"] = result["name"]
        replacement["quantity"] *= result.get(
            "quantity_factor", Fraction(1)
        )

        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        for change in result.get("wording_changes", []) or []:
            wording_changes.append((change["old"], change["new"]))

        additions, removals = _equipment_changes(result)
        equipment.difference_update(removals)
        equipment.update(additions)

        warnings.update(choice.get("warnings", []) or [])
        warnings.update(result.get("warnings", []) or [])

        next_trail = trail + (name,)
        resolved = resolve(replacement, next_trail)

        for additional in result.get("additional_ingredients", []) or []:
            extra = deepcopy(additional)
            extra["quantity"] *= scale
            resolved.extend(resolve(extra, next_trail))

        return resolved

    for section in _sections(adapted):
        ingredients = section.get("ingredients")
        if ingredients is None:
            continue

        resolved_ingredients: list[dict] = []
        for ingredient in ingredients:
            resolved_ingredients.extend(resolve(ingredient, ()))
        section["ingredients"] = resolved_ingredients

    _apply_wording(adapted, wording_changes)
    adapted["equipment"] = sorted(equipment)

    for item in equipment - available_equipment:
        reasons.add(f"equipment {item} unavailable")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
