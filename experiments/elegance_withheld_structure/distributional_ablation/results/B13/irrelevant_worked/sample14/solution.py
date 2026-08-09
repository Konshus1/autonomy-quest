from __future__ import annotations

import copy
from fractions import Fraction
from typing import Any, Iterator


def print_original(recipe: dict) -> str:
    """Return the recipe's authored representation exactly as supplied."""
    return recipe["authored_text"]


def _norm(value: Any) -> str:
    return str(value).strip().casefold()


def _items(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _ingredient_lists(value: Any) -> Iterator[list]:
    if isinstance(value, dict):
        ingredients = value.get("ingredients")
        if isinstance(ingredients, list):
            yield ingredients
        for key, child in value.items():
            if key != "ingredients":
                yield from _ingredient_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _ingredient_lists(child)


def _slots(recipe: dict) -> Iterator[tuple[list, int, dict]]:
    for ingredients in _ingredient_lists(recipe):
        for index, ingredient in enumerate(ingredients):
            if isinstance(ingredient, dict):
                yield ingredients, index, ingredient


def _rewrite_instructions(value: Any, changes: list) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "instructions" and isinstance(child, list):
                for index, statement in enumerate(child):
                    if not isinstance(statement, str):
                        continue
                    rewritten = statement
                    for change in changes:
                        if isinstance(change, dict):
                            old = str(change.get("old", ""))
                            new = str(change.get("new", ""))
                        else:
                            old, new = change
                            old, new = str(old), str(new)
                        if old:
                            rewritten = rewritten.replace(old, new)
                    child[index] = rewritten
            else:
                _rewrite_instructions(child, changes)
    elif isinstance(value, list):
        for child in value:
            _rewrite_instructions(child, changes)


def _equipment_changes(result: dict) -> tuple[list, list]:
    nested = result.get("equipment")
    nested = nested if isinstance(nested, dict) else {}

    additions = result.get("equipment_additions")
    if additions is None:
        additions = result.get("add_equipment")
    if additions is None:
        additions = nested.get("additions", nested.get("add", []))

    removals = result.get("equipment_removals")
    if removals is None:
        removals = result.get("remove_equipment")
    if removals is None:
        removals = nested.get("removals", nested.get("remove", []))

    return _items(additions), _items(removals)


def adapt(recipe: dict, request: dict, catalog: list) -> dict:
    """Adapt a recipe without modifying the recipe, request, or catalog."""
    work = copy.deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    effective_yield = Fraction(work["yield"])
    excluded = {_norm(name) for name in request.get("excluded", [])}
    available = set(request.get("available_equipment", []))

    equipment = set(work.get("equipment", []))
    choices: list = []
    warnings: set[str] = set()
    reasons: set[str] = set()

    ordered_catalog = sorted(
        enumerate(catalog),
        key=lambda item: (item[1].get("priority", 0), item[0]),
    )

    # A slot is identified by its containing list and position. Its ancestry records
    # ingredient names already replaced along that particular substitution chain.
    ancestry: dict[tuple[int, int], tuple[str, ...]] = {}
    resolved_failures: set[tuple[int, int, str]] = set()
    equipment_attempts: set[tuple[int, int, int]] = set()

    def matching_choices(ingredient: dict) -> list[tuple[int, dict]]:
        names = {_norm(ingredient.get("name", ""))}
        if "id" in ingredient:
            names.add(_norm(ingredient["id"]))
        return [
            (catalog_index, choice)
            for catalog_index, choice in ordered_catalog
            if _norm(choice.get("for", "")) in names
        ]

    def apply_choice(
        ingredients: list,
        index: int,
        choice: dict,
        parent_ancestry: tuple[str, ...],
    ) -> None:
        nonlocal effective_yield

        original = ingredients[index]
        result = choice.get("result", {})
        replacement_spec = result.get("ingredient")
        if not isinstance(replacement_spec, dict):
            replacement_spec = result

        replacement = copy.deepcopy(original)
        replacement["name"] = replacement_spec["name"]
        factor = Fraction(replacement_spec.get("quantity_factor", 1))
        replacement["quantity"] = Fraction(original["quantity"]) * factor
        if "unit" in replacement_spec:
            replacement["unit"] = replacement_spec["unit"]
        if "preparation" in replacement_spec:
            replacement["preparation"] = replacement_spec["preparation"]
        ingredients[index] = replacement

        choices.append(choice["id"])
        current_name = _norm(original.get("name", ""))
        child_ancestry = parent_ancestry + (current_name,)
        ancestry[(id(ingredients), index)] = child_ancestry

        changes = result.get("wording_changes", [])
        if changes:
            _rewrite_instructions(work, changes)

        additions, removals = _equipment_changes(result)
        equipment.difference_update(removals)
        equipment.update(additions)

        if "yield" in result:
            effective_yield = Fraction(result["yield"])
        if "yield_factor" in result:
            effective_yield *= Fraction(result["yield_factor"])

        extra = result.get("additional_ingredients", [])
        for addition in extra:
            ingredients.append(copy.deepcopy(addition))
            ancestry[(id(ingredients), len(ingredients) - 1)] = child_ancestry

    def resolve_exclusions() -> None:
        while True:
            changed = False
            for ingredients, index, ingredient in list(_slots(work)):
                name = str(ingredient.get("name", ""))
                normalized = _norm(name)
                if normalized not in excluded:
                    continue

                slot = (id(ingredients), index)
                failure = (slot[0], slot[1], normalized)
                if failure in resolved_failures:
                    continue

                history = ancestry.get(slot, ())
                if normalized in history:
                    reasons.add(f"substitution cycle involving {name}")
                    resolved_failures.add(failure)
                    continue

                candidates = matching_choices(ingredient)
                if not candidates:
                    reasons.add(f"no substitution for {name}")
                    resolved_failures.add(failure)
                    continue

                _, choice = candidates[0]
                apply_choice(ingredients, index, choice, history)
                changed = True
                break

            if not changed:
                return

    resolve_exclusions()

    # An otherwise acceptable ingredient may also be replaced when a catalog choice
    # explicitly removes equipment that is not available.
    while True:
        missing = equipment - available
        if not missing:
            break

        candidates = []
        for slot_order, (ingredients, index, ingredient) in enumerate(_slots(work)):
            for catalog_index, choice in matching_choices(ingredient):
                attempt = (id(ingredients), index, catalog_index)
                if attempt in equipment_attempts:
                    continue
                _, removals = _equipment_changes(choice.get("result", {}))
                if missing.intersection(removals):
                    candidates.append(
                        (
                            choice.get("priority", 0),
                            catalog_index,
                            slot_order,
                            ingredients,
                            index,
                            ingredient,
                            choice,
                        )
                    )

        if not candidates:
            break

        candidates.sort(key=lambda item: item[:3])
        _, catalog_index, _, ingredients, index, ingredient, choice = candidates[0]
        equipment_attempts.add((id(ingredients), index, catalog_index))
        slot = (id(ingredients), index)
        history = ancestry.get(slot, ())
        apply_choice(ingredients, index, choice, history)
        resolve_exclusions()

    # Preserve every independently applicable failure, including equipment failures
    # that coexist with dietary substitution failures.
    for _, _, ingredient in _slots(work):
        name = str(ingredient.get("name", ""))
        normalized = _norm(name)
        if normalized in excluded:
            failure_matches = any(
                item[2] == normalized for item in resolved_failures
            )
            if not failure_matches:
                reasons.add(f"no substitution for {name}")

    for item in equipment - available:
        reasons.add(f"equipment {item} unavailable")

    if effective_yield == 0:
        raise ValueError("recipe yield must not be zero")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    scale = target_yield / effective_yield
    for _, _, ingredient in _slots(work):
        ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    work["yield"] = target_yield
    work["equipment"] = sorted(equipment)

    return {
        "possible": True,
        "recipe": work,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
