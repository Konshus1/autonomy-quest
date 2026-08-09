"""Recipe adaptation with exact rational quantities."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any, Callable


def print_original(recipe: dict[str, Any]) -> str:
    """Return the recipe exactly as originally authored."""
    return recipe["authored_text"]


def adapt(
    recipe: dict[str, Any],
    request: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a deterministic adaptation without mutating any input."""
    work = deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / Fraction(recipe["yield"])
    excluded = {_key(name) for name in request.get("excluded", [])}
    available = set(request.get("available_equipment", []))

    choices: list[Any] = []
    warnings: set[str] = set()
    reasons: set[str] = set()
    wording_changes: list[tuple[str, str]] = []
    equipment = _collect_equipment(work)

    choices_by_target: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, choice in enumerate(catalog):
        target = _key(choice.get("for", ""))
        choices_by_target.setdefault(target, []).append((index, choice))

    for candidates in choices_by_target.values():
        candidates.sort(key=lambda item: (item[1]["priority"], item[0]))

    def update_equipment(result: dict[str, Any]) -> None:
        additions = _first_list(
            result,
            "equipment_additions",
            "equipment_add",
            "add_equipment",
        )
        removals = _first_list(
            result,
            "equipment_removals",
            "equipment_remove",
            "remove_equipment",
        )

        specification = result.get("equipment")
        if isinstance(specification, dict):
            additions.extend(_first_list(specification, "additions", "add"))
            removals.extend(_first_list(specification, "removals", "remove"))

        equipment.difference_update(removals)
        equipment.update(additions)

    def record_warnings(choice: dict[str, Any], result: dict[str, Any]) -> None:
        for source in (choice, result):
            value = source.get("warnings", source.get("warning", []))
            if isinstance(value, str):
                warnings.add(value)
            elif value:
                warnings.update(str(item) for item in value)

    def substitute(
        ingredient: dict[str, Any],
        lineage: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        name = str(ingredient["name"])
        normalized_name = _key(name)

        if normalized_name not in excluded:
            return [ingredient]

        if normalized_name in lineage:
            reasons.add(f"substitution cycle involving {name}")
            return []

        candidates = choices_by_target.get(normalized_name, [])
        if not candidates:
            candidates = choices_by_target.get(_key(ingredient.get("id", "")), [])

        if not candidates:
            reasons.add(f"no substitution for {name}")
            return []

        choice = candidates[0][1]
        result = choice.get("result", {})
        choices.append(choice["id"])
        record_warnings(choice, result)
        update_equipment(result)

        replacement = deepcopy(ingredient)
        replacement["name"] = result.get("name", replacement["name"])

        if "quantity_factor" in result:
            replacement["quantity"] = (
                Fraction(replacement["quantity"])
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        changes = result.get("wording_changes", [])
        if isinstance(changes, dict):
            changes = [changes]
        for change in changes:
            if isinstance(change, dict):
                wording_changes.append((str(change["old"]), str(change["new"])))
            else:
                old, new = change
                wording_changes.append((str(old), str(new)))

        next_lineage = lineage + (normalized_name,)
        adapted = substitute(replacement, next_lineage)

        additions = result.get(
            "additional_ingredients",
            result.get("additional", []),
        )
        for additional in additions:
            extra = deepcopy(additional)
            extra["quantity"] = Fraction(extra["quantity"]) * scale
            adapted.extend(substitute(extra, next_lineage))

        return adapted

    def transform(ingredient: dict[str, Any]) -> list[dict[str, Any]]:
        scaled = deepcopy(ingredient)
        scaled["quantity"] = Fraction(scaled["quantity"]) * scale
        return substitute(scaled, ())

    _replace_ingredient_lists(work, transform)
    _scale_component_yields(work, scale, is_root=True)
    work["yield"] = target_yield
    _rewrite_instructions(work, wording_changes)
    _normalize_equipment(work, sorted(equipment), is_root=True)

    for name in equipment - available:
        reasons.add(f"equipment {name} unavailable")

    sorted_reasons = sorted(reasons)
    possible = not sorted_reasons
    return {
        "possible": possible,
        "recipe": work if possible else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted_reasons,
    }


def _key(value: Any) -> str:
    return str(value).strip().casefold()


def _first_list(mapping: dict[str, Any], *names: str) -> list[Any]:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return list(mapping[name])
    return []


def _replace_ingredient_lists(
    value: Any,
    transform: Callable[[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    if isinstance(value, dict):
        ingredients = value.get("ingredients")
        if isinstance(ingredients, list):
            replacement: list[dict[str, Any]] = []
            for ingredient in ingredients:
                replacement.extend(transform(ingredient))
            value["ingredients"] = replacement

        for key, child in value.items():
            if key not in {"ingredients", "authored_text"}:
                _replace_ingredient_lists(child, transform)
    elif isinstance(value, list):
        for child in value:
            _replace_ingredient_lists(child, transform)


def _collect_equipment(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        equipment = value.get("equipment")
        if isinstance(equipment, list):
            found.update(equipment)
        for key, child in value.items():
            if key not in {"equipment", "authored_text"}:
                found.update(_collect_equipment(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_collect_equipment(child))
    return found


def _normalize_equipment(
    value: Any,
    equipment: list[str],
    is_root: bool,
) -> None:
    if isinstance(value, dict):
        if is_root:
            value["equipment"] = list(equipment)
        elif "equipment" in value:
            value["equipment"] = []

        for key, child in value.items():
            if key not in {"equipment", "authored_text"}:
                _normalize_equipment(child, equipment, False)
    elif isinstance(value, list):
        for child in value:
            _normalize_equipment(child, equipment, False)


def _scale_component_yields(
    value: Any,
    scale: Fraction,
    is_root: bool = False,
) -> None:
    if isinstance(value, dict):
        if not is_root and "yield" in value:
            value["yield"] = Fraction(value["yield"]) * scale
        for key, child in value.items():
            if key not in {"yield", "authored_text"}:
                _scale_component_yields(child, scale)
    elif isinstance(value, list):
        for child in value:
            _scale_component_yields(child, scale)


def _rewrite_instructions(
    value: Any,
    changes: list[tuple[str, str]],
) -> None:
    if isinstance(value, dict):
        instructions = value.get("instructions")
        if isinstance(instructions, list):
            rewritten: list[str] = []
            for instruction in instructions:
                text = instruction
                for old, new in changes:
                    text = text.replace(old, new)
                rewritten.append(text)
            value["instructions"] = rewritten

        for key, child in value.items():
            if key not in {"instructions", "authored_text"}:
                _rewrite_instructions(child, changes)
    elif isinstance(value, list):
        for child in value:
            _rewrite_instructions(child, changes)
