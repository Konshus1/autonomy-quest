from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any


def _key(value: Any) -> str:
    return str(value).casefold()


def print_original(recipe: dict) -> str:
    """Return the recipe's original authored representation unchanged."""
    return recipe["authored_text"]


def adapt(recipe: dict, request: dict, catalog: list[dict]) -> dict:
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    excluded = {_key(name) for name in request["excluded"]}
    available = {_key(item) for item in request["available_equipment"]}
    indexed_catalog = list(enumerate(catalog))

    choices: list[str] = []
    warnings: list[str] = []
    reasons: set[str] = set()
    wording_changes: list[tuple[str, str]] = []
    equipment = set(adapted.get("equipment", []))

    yield_factor = Fraction(1)
    yield_override: Fraction | None = None

    def collect_warnings(value: Any) -> None:
        if not isinstance(value, dict):
            return
        supplied = value.get("warnings", value.get("warning", []))
        if isinstance(supplied, str):
            warnings.append(supplied)
        elif supplied:
            warnings.extend(str(item) for item in supplied)

    def matching_choices(ingredient: dict) -> list[dict]:
        name = _key(ingredient.get("name", ""))
        ingredient_id = _key(ingredient.get("id", ""))
        matches: list[tuple[Any, int, dict]] = []

        for position, choice in indexed_catalog:
            target = choice.get("for")
            targets = target if isinstance(target, list) else [target]
            if any(_key(item) in (name, ingredient_id) for item in targets):
                matches.append((choice.get("priority", 0), position, choice))

        matches.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in matches]

    def result_equipment(result: dict, addition: bool) -> list[str]:
        if addition:
            return result.get(
                "equipment_additions",
                result.get("equipment_add", result.get("add_equipment", [])),
            ) or []
        return result.get(
            "equipment_removals",
            result.get("equipment_remove", result.get("remove_equipment", [])),
        ) or []

    def resolve(ingredient: dict, ancestry: tuple[str, ...] = ()) -> list[dict]:
        nonlocal yield_factor, yield_override

        current = deepcopy(ingredient)
        name = str(current.get("name", ""))
        normalized_name = _key(name)

        if normalized_name not in excluded:
            return [current]

        if normalized_name in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            return []

        candidates = matching_choices(current)
        if not candidates:
            reasons.add(f"no substitution for {name}")
            return []

        choice = candidates[0]
        result = choice.get("result") or {}
        choices.append(choice["id"])
        collect_warnings(choice)
        collect_warnings(result)

        replacement = deepcopy(current)
        replacement["name"] = result["name"]

        if "quantity_factor" in result:
            replacement["quantity"] *= result["quantity_factor"]
        if "unit" in result:
            replacement["unit"] = deepcopy(result["unit"])
        if "preparation" in result:
            replacement["preparation"] = deepcopy(result["preparation"])

        changes = result.get("wording_changes", []) or []
        if isinstance(changes, dict):
            changes = changes.items()
        for change in changes:
            if isinstance(change, dict):
                old, new = change["old"], change["new"]
            else:
                old, new = change
            wording_changes.append((str(old), str(new)))

        equipment.difference_update(result_equipment(result, addition=False))
        equipment.update(result_equipment(result, addition=True))

        if "yield_factor" in result:
            yield_factor *= result["yield_factor"]
        if "yield" in result:
            yield_override = result["yield"]

        next_ancestry = ancestry + (normalized_name,)
        resolved = resolve(replacement, next_ancestry)

        additions = result.get(
            "additional_ingredients", result.get("additional", [])
        ) or []
        for additional in additions:
            resolved.extend(resolve(additional, next_ancestry))

        return resolved

    def resolve_ingredient_lists(value: Any) -> None:
        if isinstance(value, dict):
            for key in list(value):
                child = value[key]
                if key == "ingredients" and isinstance(child, list):
                    resolved: list[dict] = []
                    for ingredient in child:
                        resolved.extend(resolve(ingredient))
                    value[key] = resolved
                elif key != "authored_text":
                    resolve_ingredient_lists(child)
        elif isinstance(value, list):
            for child in value:
                resolve_ingredient_lists(child)

    resolve_ingredient_lists(adapted)

    effective_yield = (
        yield_override
        if yield_override is not None
        else recipe["yield"] * yield_factor
    )
    scale = request["target_yield"] / effective_yield

    def finalize(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in list(value.items()):
                if key == "ingredients" and isinstance(child, list):
                    for ingredient in child:
                        ingredient["quantity"] *= scale
                elif key == "instructions" and isinstance(child, list):
                    edited: list[str] = []
                    for authored_line in child:
                        line = authored_line
                        for old, new in wording_changes:
                            line = line.replace(old, new)
                        edited.append(line)
                    value[key] = edited
                elif key != "authored_text":
                    finalize(child)
        elif isinstance(value, list):
            for child in value:
                finalize(child)

    finalize(adapted)
    adapted["yield"] = deepcopy(request["target_yield"])
    adapted["equipment"] = sorted(equipment)

    for item in equipment:
        if _key(item) not in available:
            reasons.add(f"equipment {item} unavailable")

    sorted_reasons = sorted(reasons)
    possible = not sorted_reasons

    return {
        "possible": possible,
        "recipe": adapted if possible else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted_reasons,
    }
