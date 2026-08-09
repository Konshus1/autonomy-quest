from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any


def _containers(recipe: dict[str, Any]):
    """Yield the recipe and all nested component dictionaries in authored order."""
    yield recipe
    for component in recipe.get("components") or []:
        if isinstance(component, dict):
            yield from _containers(component)


def _unique_sorted(values: list[str]) -> list[str]:
    return sorted(dict.fromkeys(values))


def adapt(
    recipe: dict[str, Any],
    request: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a non-mutating adaptation of recipe for request."""
    adapted = deepcopy(recipe)
    target_yield = request["target_yield"]
    scale = target_yield / adapted["yield"]
    adapted["yield"] = target_yield

    containers = list(_containers(adapted))

    for container in containers:
        if container is not adapted and "yield" in container:
            container["yield"] *= scale
        for ingredient in container.get("ingredients") or []:
            ingredient["quantity"] *= scale

    excluded = set(request.get("excluded") or [])
    available_equipment = set(request.get("available_equipment") or [])

    catalog_by_name: dict[str, list[tuple[Any, int, dict[str, Any]]]] = {}
    for position, choice in enumerate(catalog):
        catalog_by_name.setdefault(choice["for"], []).append(
            (choice["priority"], position, choice)
        )
    for options in catalog_by_name.values():
        options.sort(key=lambda item: (item[0], item[1]))

    chosen_ids: list[str] = []
    reasons: set[str] = set()
    wording_changes: list[tuple[str, str]] = []
    equipment_additions: list[str] = []
    equipment_removals: list[str] = []

    def resolve_ingredient(
        ingredient: dict[str, Any], path: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        name = ingredient["name"]
        if name not in excluded:
            return [ingredient]

        if name in path:
            reasons.add(f"substitution cycle involving {name}")
            return [ingredient]

        options = catalog_by_name.get(name)
        if not options:
            reasons.add(f"no substitution for {name}")
            return [ingredient]

        choice = options[0][2]
        chosen_ids.append(choice["id"])
        result = choice["result"]

        replacement = deepcopy(ingredient)
        replacement["name"] = result["name"]
        replacement["quantity"] *= result.get("quantity_factor", Fraction(1))

        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        for change in result.get("wording_changes") or []:
            wording_changes.append((change["old"], change["new"]))

        equipment_removals.extend(
            result.get("equipment_removals", result.get("remove_equipment", [])) or []
        )
        equipment_additions.extend(
            result.get("equipment_additions", result.get("add_equipment", [])) or []
        )

        next_path = path + (name,)
        produced = resolve_ingredient(replacement, next_path)

        additional = result.get(
            "additional_ingredients", result.get("additional", [])
        ) or []
        for extra in additional:
            extra_copy = deepcopy(extra)
            extra_copy["quantity"] *= scale
            produced.extend(resolve_ingredient(extra_copy, next_path))

        return produced

    for container in containers:
        if "ingredients" not in container:
            continue
        resolved: list[dict[str, Any]] = []
        for ingredient in container.get("ingredients") or []:
            resolved.extend(resolve_ingredient(ingredient, ()))
        container["ingredients"] = resolved

    for container in containers:
        if "instructions" not in container:
            continue
        rewritten: list[str] = []
        for authored_line in container.get("instructions") or []:
            line = authored_line
            for old, new in wording_changes:
                line = line.replace(old, new)
            rewritten.append(line)
        container["instructions"] = rewritten

    equipment = list(adapted.get("equipment") or [])
    for removed in equipment_removals:
        equipment = [item for item in equipment if item != removed]
    equipment.extend(equipment_additions)
    adapted["equipment"] = _unique_sorted(equipment)

    # Nested components may carry their own explicit equipment lists.
    for container in containers[1:]:
        if "equipment" in container:
            container["equipment"] = _unique_sorted(
                list(container.get("equipment") or [])
            )

    for container in containers:
        for item in container.get("equipment") or []:
            if item not in available_equipment:
                reasons.add(f"equipment {item} unavailable")

    sorted_reasons = sorted(reasons)
    possible = not sorted_reasons
    return {
        "possible": possible,
        "recipe": adapted if possible else None,
        "choices": chosen_ids,
        "warnings": [],
        "reasons": sorted_reasons,
    }


def print_original(recipe: dict[str, Any]) -> str:
    """Return the recipe's authored representation byte-for-byte."""
    return recipe["authored_text"]
