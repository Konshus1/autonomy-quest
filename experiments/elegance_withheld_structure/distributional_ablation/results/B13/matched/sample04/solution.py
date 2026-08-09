from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterator


def print_original(recipe: dict) -> str:
    """Return the recipe's original authored representation unchanged."""
    return recipe["authored_text"]


def _ingredient_lists(value: Any) -> Iterator[list]:
    """Yield every ingredient list in a recipe and its components."""
    if isinstance(value, dict):
        ingredients = value.get("ingredients")
        if isinstance(ingredients, list):
            yield ingredients

        components = value.get("components")
        if isinstance(components, (list, dict)):
            yield from _ingredient_lists(components)
    elif isinstance(value, list):
        for item in value:
            yield from _ingredient_lists(item)


def _ingredients(recipe: dict) -> Iterator[dict]:
    for ingredient_list in _ingredient_lists(recipe):
        for ingredient in ingredient_list:
            if isinstance(ingredient, dict):
                yield ingredient


def _instruction_lists(value: Any) -> Iterator[list]:
    if isinstance(value, dict):
        instructions = value.get("instructions")
        if isinstance(instructions, list):
            yield instructions

        components = value.get("components")
        if isinstance(components, (list, dict)):
            yield from _instruction_lists(components)
    elif isinstance(value, list):
        for item in value:
            yield from _instruction_lists(item)


def _equipment_lists(value: Any) -> Iterator[list]:
    if isinstance(value, dict):
        equipment = value.get("equipment")
        if isinstance(equipment, list):
            yield equipment

        components = value.get("components")
        if isinstance(components, (list, dict)):
            yield from _equipment_lists(components)
    elif isinstance(value, list):
        for item in value:
            yield from _equipment_lists(item)


def _normal(value: Any) -> str:
    return str(value).casefold()


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _equipment_effects(result: dict) -> tuple[list, list]:
    additions = []
    removals = []

    additions.extend(_as_list(result.get("equipment_additions")))
    additions.extend(_as_list(result.get("add_equipment")))
    additions.extend(_as_list(result.get("equipment_add")))

    removals.extend(_as_list(result.get("equipment_removals")))
    removals.extend(_as_list(result.get("remove_equipment")))
    removals.extend(_as_list(result.get("equipment_remove")))

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions.extend(_as_list(equipment.get("add")))
        removals.extend(_as_list(equipment.get("remove")))

    return additions, removals


def adapt(recipe: dict, request: dict, catalog: list[dict]) -> dict:
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    original_yield = Fraction(recipe["yield"])

    if original_yield == 0:
        raise ValueError("recipe yield must not be zero")

    initial_scale = target_yield / original_yield
    for ingredient in _ingredients(adapted):
        ingredient["quantity"] = Fraction(ingredient["quantity"]) * initial_scale

    excluded = {_normal(name) for name in request.get("excluded", [])}
    available = {_normal(name) for name in request.get("available_equipment", [])}

    indexed_catalog = list(enumerate(catalog))

    def find_choice(ingredient: dict) -> dict | None:
        name = _normal(ingredient.get("name", ""))
        ingredient_id = _normal(ingredient.get("id", ""))
        matches = [
            (choice.get("priority", 0), index, choice)
            for index, choice in indexed_catalog
            if _normal(choice.get("for", "")) in {name, ingredient_id}
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: (item[0], item[1]))
        return matches[0][2]

    root_equipment = set(adapted.get("equipment", []))
    choices: list = []
    warnings: set[str] = set()
    reasons: set[str] = set()
    wording_changes: list[tuple[str, str]] = []
    effective_yield = original_yield

    # Each queue entry carries the ingredient's own substitution ancestry. This
    # permits independent ingredients with the same name while detecting chains.
    queue: list[tuple[dict, list, frozenset[str]]] = []
    for ingredient_list in _ingredient_lists(adapted):
        for ingredient in ingredient_list:
            if isinstance(ingredient, dict):
                queue.append((ingredient, ingredient_list, frozenset()))

    position = 0
    while position < len(queue):
        ingredient, containing_list, ancestry = queue[position]
        position += 1

        name = str(ingredient.get("name", ""))
        normalized_name = _normal(name)
        if normalized_name not in excluded:
            continue

        if normalized_name in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            continue

        choice = find_choice(ingredient)
        if choice is None:
            reasons.add(f"no substitution for {name}")
            continue

        choices.append(choice["id"])
        result = choice.get("result", {})
        new_ancestry = ancestry | {normalized_name}

        replacement_name = result.get("name", name)
        ingredient["name"] = replacement_name
        ingredient["quantity"] = Fraction(ingredient["quantity"]) * Fraction(
            result.get("quantity_factor", 1)
        )
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        for change in result.get("wording_changes", []):
            wording_changes.append((str(change["old"]), str(change["new"])))

        additions, removals = _equipment_effects(result)
        root_equipment.difference_update(removals)
        root_equipment.update(additions)

        warnings.update(str(item) for item in _as_list(choice.get("warnings")))
        warnings.update(str(item) for item in _as_list(result.get("warnings")))

        if "yield" in result:
            effective_yield = Fraction(result["yield"])
        if "yield_factor" in result:
            effective_yield *= Fraction(result["yield_factor"])

        # Catalog quantities describe the unscaled source recipe, so introduced
        # ingredients receive the same initial yield scale as authored ones.
        for additional in result.get("additional_ingredients", []):
            introduced = deepcopy(additional)
            introduced["quantity"] = (
                Fraction(introduced["quantity"]) * initial_scale
            )
            containing_list.append(introduced)
            queue.append((introduced, containing_list, new_ancestry))

        replacement_normal = _normal(replacement_name)
        if replacement_normal in excluded:
            if replacement_normal in new_ancestry:
                reasons.add(
                    f"substitution cycle involving {replacement_name}"
                )
            else:
                queue.append((ingredient, containing_list, new_ancestry))

    if effective_yield == 0:
        raise ValueError("substitution yield must not be zero")

    # Initial scaling assumed the original yield. Correct all quantities if a
    # selected substitution changed the effective source yield.
    yield_correction = original_yield / effective_yield
    if yield_correction != 1:
        for ingredient in _ingredients(adapted):
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"]) * yield_correction
            )

    for instructions in _instruction_lists(adapted):
        for index, statement in enumerate(instructions):
            changed = statement
            for old, new in wording_changes:
                changed = changed.replace(old, new)
            instructions[index] = changed

    adapted["yield"] = target_yield
    adapted["equipment"] = sorted(root_equipment)

    # A final scan guarantees that exclusions introduced at any depth are
    # represented among the impossibility reasons.
    for ingredient in _ingredients(adapted):
        name = str(ingredient.get("name", ""))
        if _normal(name) in excluded:
            has_specific_reason = any(
                reason == f"no substitution for {name}"
                or reason == f"substitution cycle involving {name}"
                for reason in reasons
            )
            if not has_specific_reason:
                reasons.add(f"no substitution for {name}")

    for equipment_list in _equipment_lists(adapted):
        equipment_list[:] = sorted(set(equipment_list))
        for equipment in equipment_list:
            if _normal(equipment) not in available:
                reasons.add(f"equipment {equipment} unavailable")

    possible = not reasons
    return {
        "possible": possible,
        "recipe": adapted if possible else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }
