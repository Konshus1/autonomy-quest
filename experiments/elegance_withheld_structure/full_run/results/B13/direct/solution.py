from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterable


def print_original(recipe: dict) -> str:
    """Return the recipe exactly as originally authored."""
    return recipe["authored_text"]


def _fraction(value: Any) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if isinstance(value, float):
        return Fraction(str(value))
    return Fraction(value)


def _walk_dicts(value: Any) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _ingredient_entries(recipe: dict) -> list[tuple[dict, list]]:
    entries: list[tuple[dict, list]] = []
    for container in _walk_dicts(recipe):
        ingredients = container.get("ingredients")
        if isinstance(ingredients, list):
            for ingredient in ingredients:
                if isinstance(ingredient, dict):
                    entries.append((ingredient, ingredients))
    return entries


def _equipment_lists(recipe: dict) -> list[list]:
    result: list[list] = []
    for container in _walk_dicts(recipe):
        equipment = container.get("equipment")
        if isinstance(equipment, list):
            result.append(equipment)
    return result


def _matches(choice: dict, ingredient: dict) -> bool:
    target = choice.get("for")
    if isinstance(target, (list, tuple, set)):
        return ingredient.get("name") in target or ingredient.get("id") in target
    return target == ingredient.get("name") or target == ingredient.get("id")


def _choice_key(indexed_choice: tuple[int, dict]) -> tuple[Any, int]:
    index, choice = indexed_choice
    return choice.get("priority", float("inf")), index


def _result(choice: dict) -> dict:
    result = choice.get("result", {})
    nested = result.get("ingredient")
    if isinstance(nested, dict):
        merged = dict(result)
        merged.update(nested)
        return merged
    return result


def _equipment_values(result: dict, kind: str) -> list:
    if kind == "add":
        keys = ("equipment_additions", "add_equipment")
    else:
        keys = ("equipment_removals", "remove_equipment")
    values: list = []
    for key in keys:
        value = result.get(key, [])
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(value)
    return values


def adapt(recipe: dict, request: dict, catalog: list[dict]) -> dict:
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    indexed_catalog = list(enumerate(catalog))
    indexed_catalog.sort(key=_choice_key)

    excluded = set(request.get("excluded", []))
    available = set(request.get("available_equipment", []))
    choices: list = []
    warnings: set[str] = set()
    reasons: set[str] = set()
    wording_changes: list[tuple[str, str]] = []
    effective_yield = _fraction(adapted["yield"])

    entries = _ingredient_entries(adapted)
    pending: list[tuple[dict, list]] = list(entries)
    lineages: dict[int, tuple[str, ...]] = {id(item): () for item, _ in entries}
    attempted_equipment_choices: set[tuple[int, int]] = set()

    def apply_choice(
        ingredient: dict,
        owner: list,
        catalog_index: int,
        choice: dict,
    ) -> None:
        nonlocal effective_yield

        result = _result(choice)
        old_name = str(ingredient.get("name", ingredient.get("id", "")))
        lineage = lineages.get(id(ingredient), ())

        choices.append(choice.get("id"))
        new_name = result.get("name", ingredient.get("name"))
        ingredient["name"] = new_name

        if "quantity_factor" in result:
            ingredient["quantity"] = (
                _fraction(ingredient["quantity"])
                * _fraction(result["quantity_factor"])
            )
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        new_lineage = lineage + (old_name,)
        lineages[id(ingredient)] = new_lineage

        changes = result.get("wording_changes", [])
        if isinstance(changes, dict):
            changes = [changes]
        for change in changes:
            if isinstance(change, dict) and "old" in change and "new" in change:
                wording_changes.append((str(change["old"]), str(change["new"])))

        removals = set(_equipment_values(result, "remove"))
        if removals:
            for equipment in _equipment_lists(adapted):
                equipment[:] = [name for name in equipment if name not in removals]

        additions = _equipment_values(result, "add")
        root_equipment = adapted.setdefault("equipment", [])
        for name in additions:
            if name not in root_equipment:
                root_equipment.append(name)

        additional_ingredients = result.get("additional_ingredients", [])
        if isinstance(additional_ingredients, dict):
            additional_ingredients = [additional_ingredients]
        for additional in additional_ingredients:
            if not isinstance(additional, dict):
                continue
            new_ingredient = deepcopy(additional)
            if "quantity" in new_ingredient:
                new_ingredient["quantity"] = _fraction(new_ingredient["quantity"])
            owner.append(new_ingredient)
            lineages[id(new_ingredient)] = new_lineage
            pending.append((new_ingredient, owner))

        if "yield_factor" in result:
            effective_yield *= _fraction(result["yield_factor"])
        if "yield" in result:
            effective_yield = _fraction(result["yield"])

        pending.append((ingredient, owner))

    def process_exclusions() -> None:
        while pending:
            ingredient, owner = pending.pop(0)
            name = ingredient.get("name")
            ingredient_id = ingredient.get("id")
            if name not in excluded and ingredient_id not in excluded:
                continue

            display_name = str(name if name is not None else ingredient_id)
            lineage = lineages.get(id(ingredient), ())
            if display_name in lineage:
                reasons.add(f"substitution cycle involving {display_name}")
                continue

            applicable = [
                (index, choice)
                for index, choice in indexed_catalog
                if _matches(choice, ingredient)
            ]
            if not applicable:
                reasons.add(f"no substitution for {display_name}")
                continue

            index, choice = applicable[0]
            result = _result(choice)
            replacement_name = str(result.get("name", display_name))
            if replacement_name in lineage:
                reasons.add(f"substitution cycle involving {replacement_name}")
                continue
            apply_choice(ingredient, owner, index, choice)

    process_exclusions()

    # A catalog entry that explicitly removes unavailable equipment can also
    # serve as an equipment-driven adaptation.
    while True:
        unavailable = {
            name
            for equipment in _equipment_lists(adapted)
            for name in equipment
            if name not in available
        }
        candidates: list[tuple[Any, int, int, dict, list, dict]] = []
        for ingredient_order, (ingredient, owner) in enumerate(
            _ingredient_entries(adapted)
        ):
            for index, choice in indexed_catalog:
                marker = (id(ingredient), index)
                if marker in attempted_equipment_choices:
                    continue
                if not _matches(choice, ingredient):
                    continue
                removals = set(_equipment_values(_result(choice), "remove"))
                if removals & unavailable:
                    candidates.append(
                        (
                            choice.get("priority", float("inf")),
                            index,
                            ingredient_order,
                            ingredient,
                            owner,
                            choice,
                        )
                    )

        if not candidates:
            break

        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        _, index, _, ingredient, owner, choice = candidates[0]
        attempted_equipment_choices.add((id(ingredient), index))

        current_name = str(ingredient.get("name", ingredient.get("id", "")))
        replacement_name = str(_result(choice).get("name", current_name))
        lineage = lineages.get(id(ingredient), ())
        if replacement_name in lineage:
            reasons.add(f"substitution cycle involving {replacement_name}")
            continue

        apply_choice(ingredient, owner, index, choice)
        process_exclusions()

    for equipment in _equipment_lists(adapted):
        equipment[:] = sorted(dict.fromkeys(equipment))
        for name in equipment:
            if name not in available:
                reasons.add(f"equipment {name} unavailable")

    for container in _walk_dicts(adapted):
        instructions = container.get("instructions")
        if not isinstance(instructions, list):
            continue
        updated: list = []
        for instruction in instructions:
            if not isinstance(instruction, str):
                updated.append(instruction)
                continue
            text = instruction
            for old, new in wording_changes:
                text = text.replace(old, new)
            updated.append(text)
        container["instructions"] = updated

    target_yield = _fraction(request["target_yield"])
    if effective_yield == 0:
        raise ValueError("recipe yield must not be zero")
    scale = target_yield / effective_yield
    for ingredient, _ in _ingredient_entries(adapted):
        if "quantity" in ingredient:
            ingredient["quantity"] = _fraction(ingredient["quantity"]) * scale
    adapted["yield"] = target_yield

    sorted_reasons = sorted(reasons)
    return {
        "possible": not sorted_reasons,
        "recipe": adapted if not sorted_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted_reasons,
    }


__all__ = ["adapt", "print_original"]
