# solution.py / recipebook.py
from __future__ import annotations

from collections import deque
from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterable


def _key(value: Any) -> str:
    return str(value).strip().casefold()


def _values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _ingredient_lists(recipe: dict[str, Any]) -> list[list[dict[str, Any]]]:
    found: list[list[dict[str, Any]]] = []

    def visit(section: Any) -> None:
        if not isinstance(section, dict):
            return
        ingredients = section.get("ingredients")
        if isinstance(ingredients, list):
            found.append(ingredients)
        components = section.get("components", [])
        if isinstance(components, dict):
            components = list(components.values())
        if isinstance(components, list):
            for component in components:
                visit(component)

    visit(recipe)
    return found


def _rewrite_instructions(section: Any, changes: list[tuple[str, str]]) -> None:
    if not isinstance(section, dict):
        return

    instructions = section.get("instructions")
    if isinstance(instructions, list):
        rewritten = []
        for instruction in instructions:
            if isinstance(instruction, str):
                for old, new in changes:
                    instruction = instruction.replace(old, new)
            rewritten.append(instruction)
        section["instructions"] = rewritten

    components = section.get("components", [])
    if isinstance(components, dict):
        components = list(components.values())
    if isinstance(components, list):
        for component in components:
            _rewrite_instructions(component, changes)


def _equipment_effect(result: dict[str, Any], kind: str) -> list[Any]:
    if kind == "add":
        keys = ("equipment_additions", "add_equipment", "equipment_added")
    else:
        keys = ("equipment_removals", "remove_equipment", "equipment_removed")
    collected: list[Any] = []
    for key in keys:
        collected.extend(_values(result.get(key)))
    return collected


def _additional_ingredients(result: dict[str, Any]) -> list[dict[str, Any]]:
    value = result.get("additional_ingredients", result.get("additional", []))
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [deepcopy(item) for item in value if isinstance(item, dict)]


def adapt(recipe: dict, request: dict, catalog: Iterable[dict]) -> dict:
    work = deepcopy(recipe)
    choices: list[Any] = []
    warnings: set[str] = set()
    reasons: set[str] = set()

    excluded = {_key(item) for item in request.get("excluded", [])}
    available = {_key(item) for item in request.get("available_equipment", [])}

    indexed_catalog = []
    for index, choice in enumerate(catalog or []):
        if not isinstance(choice, dict):
            continue
        indexed_catalog.append(
            (choice.get("priority", 0), index, choice)
        )
    indexed_catalog.sort(key=lambda item: (item[0], item[1]))

    by_target: dict[str, list[dict[str, Any]]] = {}
    for _, _, choice in indexed_catalog:
        by_target.setdefault(_key(choice.get("for", "")), []).append(choice)

    equipment = list(deepcopy(work.get("equipment", [])))
    wording_changes: list[tuple[str, str]] = []

    current_yield = work.get("yield", Fraction(1))
    if not isinstance(current_yield, Fraction):
        current_yield = Fraction(current_yield)

    queue = deque()
    for ingredient_list in _ingredient_lists(work):
        for ingredient in ingredient_list:
            if isinstance(ingredient, dict):
                queue.append((ingredient, ingredient_list, ()))

    while queue:
        ingredient, owner, ancestry = queue.popleft()
        name = str(ingredient.get("name", ""))
        normalized_name = _key(name)
        if normalized_name not in excluded:
            continue

        if normalized_name in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            continue

        candidates = by_target.get(normalized_name, [])
        if not candidates:
            ingredient_id = _key(ingredient.get("id", ""))
            candidates = by_target.get(ingredient_id, [])

        if not candidates:
            reasons.add(f"no substitution for {name}")
            continue

        choice = candidates[0]
        choices.append(choice.get("id"))
        result = choice.get("result", {})
        if not isinstance(result, dict):
            result = {}

        replacement = result.get("ingredient", result)
        if not isinstance(replacement, dict):
            replacement = {}

        if "name" in replacement:
            ingredient["name"] = deepcopy(replacement["name"])
        if "quantity_factor" in replacement:
            ingredient["quantity"] = (
                ingredient.get("quantity", Fraction(0))
                * replacement["quantity_factor"]
            )
        if "unit" in replacement:
            ingredient["unit"] = deepcopy(replacement["unit"])
        if "preparation" in replacement:
            ingredient["preparation"] = deepcopy(replacement["preparation"])

        for change in _values(result.get("wording_changes", [])):
            if isinstance(change, dict) and "old" in change and "new" in change:
                wording_changes.append((str(change["old"]), str(change["new"])))

        removed = {_key(item) for item in _equipment_effect(result, "remove")}
        if removed:
            equipment = [item for item in equipment if _key(item) not in removed]
        for item in _equipment_effect(result, "add"):
            if _key(item) not in {_key(existing) for existing in equipment}:
                equipment.append(deepcopy(item))

        for warning in _values(choice.get("warnings")) + _values(result.get("warnings")):
            warnings.add(str(warning))
        for warning in _values(choice.get("warning")) + _values(result.get("warning")):
            warnings.add(str(warning))

        if "yield" in result:
            current_yield = result["yield"]
            if not isinstance(current_yield, Fraction):
                current_yield = Fraction(current_yield)
        if "yield_factor" in result:
            current_yield *= result["yield_factor"]

        additions = _additional_ingredients(result)
        owner.extend(additions)

        next_ancestry = ancestry + (normalized_name,)
        followups = [(ingredient, owner, next_ancestry)]
        followups.extend((item, owner, next_ancestry) for item in additions)
        for followup in reversed(followups):
            queue.appendleft(followup)

    _rewrite_instructions(work, wording_changes)

    target_yield = request.get("target_yield", current_yield)
    if not isinstance(target_yield, Fraction):
        target_yield = Fraction(target_yield)
    scale = target_yield / current_yield

    for ingredient_list in _ingredient_lists(work):
        for ingredient in ingredient_list:
            if isinstance(ingredient, dict) and "quantity" in ingredient:
                ingredient["quantity"] = ingredient["quantity"] * scale

    work["yield"] = target_yield
    work["equipment"] = sorted(equipment, key=lambda item: str(item))

    for ingredient_list in _ingredient_lists(work):
        for ingredient in ingredient_list:
            if isinstance(ingredient, dict):
                name = str(ingredient.get("name", ""))
                if _key(name) in excluded:
                    normalized_reason_name = name
                    cycle_reason = f"substitution cycle involving {normalized_reason_name}"
                    no_sub_reason = f"no substitution for {normalized_reason_name}"
                    if cycle_reason not in reasons and no_sub_reason not in reasons:
                        reasons.add(no_sub_reason)

    for item in work["equipment"]:
        if _key(item) not in available:
            reasons.add(f"equipment {item} unavailable")

    sorted_reasons = sorted(reasons)
    return {
        "possible": not sorted_reasons,
        "recipe": work if not sorted_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted_reasons,
    }


def print_original(recipe: dict) -> str:
    return recipe["authored_text"]
