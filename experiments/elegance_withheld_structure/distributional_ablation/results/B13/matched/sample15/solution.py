"""Recipe adaptation using exact rational quantities."""

from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterable


def print_original(recipe: dict) -> str:
    """Return the recipe exactly as authored."""
    return recipe["authored_text"]


def _list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _normalized(values: Iterable[Any]) -> set[str]:
    return {str(value).casefold() for value in values}


def _sections(recipe: dict) -> list[dict]:
    result = [recipe]
    for component in recipe.get("components", []):
        if isinstance(component, dict):
            result.extend(_sections(component))
    return result


def _ingredient_groups(recipe: dict) -> list[list[dict]]:
    groups = []
    for section in _sections(recipe):
        ingredients = section.get("ingredients")
        if isinstance(ingredients, list):
            groups.append(ingredients)
    return groups


def _catalog_choices(catalog: Any) -> list[dict]:
    if isinstance(catalog, dict):
        catalog = catalog.get("choices", catalog.get("substitutions", []))
    if not isinstance(catalog, list):
        return []

    indexed = [
        (index, choice)
        for index, choice in enumerate(catalog)
        if isinstance(choice, dict)
    ]
    indexed.sort(key=lambda item: (item[1].get("priority", 0), item[0]))
    return [choice for _, choice in indexed]


def _find_choice(ingredient: dict, choices: list[dict]) -> dict | None:
    keys = {str(ingredient.get("name", "")).casefold()}
    if ingredient.get("id") is not None:
        keys.add(str(ingredient["id"]).casefold())

    for choice in choices:
        targets = _list(choice.get("for"))
        if any(str(target).casefold() in keys for target in targets):
            return choice
    return None


def _apply_wording(recipe: dict, changes: Any) -> None:
    pairs = []
    for change in _list(changes):
        if isinstance(change, dict) and "old" in change and "new" in change:
            pairs.append((str(change["old"]), str(change["new"])))
        elif isinstance(change, (list, tuple)) and len(change) == 2:
            pairs.append((str(change[0]), str(change[1])))

    for section in _sections(recipe):
        instructions = section.get("instructions")
        if not isinstance(instructions, list):
            continue

        rewritten = []
        for instruction in instructions:
            if isinstance(instruction, str):
                for old, new in pairs:
                    instruction = instruction.replace(old, new)
            rewritten.append(instruction)
        section["instructions"] = rewritten


def _equipment_values(result: dict, kind: str) -> list:
    if kind == "add":
        keys = ("equipment_additions", "equipment_add", "add_equipment")
    else:
        keys = ("equipment_removals", "equipment_remove", "remove_equipment")

    for key in keys:
        if key in result:
            return _list(result[key])
    return []


def _additional_ingredients(result: dict) -> list[dict]:
    values = result.get("additional_ingredients", [])
    return [deepcopy(value) for value in _list(values) if isinstance(value, dict)]


def adapt(recipe: dict, request: dict, catalog: Any) -> dict:
    """Adapt a recipe without modifying recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    choices = _catalog_choices(catalog)
    excluded = _normalized(request.get("excluded", []))
    selected: list[str] = []
    warnings: set[str] = set()
    reasons: set[str] = set()

    equipment = {
        str(item) for item in _list(adapted.get("equipment", []))
    }
    groups = _ingredient_groups(adapted)

    for group in groups:
        index = 0
        while index < len(group):
            ingredient = group[index]
            path: set[str] = set()

            while str(ingredient.get("name", "")).casefold() in excluded:
                name = str(ingredient.get("name", ""))
                normalized_name = name.casefold()

                if normalized_name in path:
                    reasons.add(f"substitution cycle involving {name}")
                    break
                path.add(normalized_name)

                choice = _find_choice(ingredient, choices)
                if choice is None:
                    reasons.add(f"no substitution for {name}")
                    break

                result = choice.get("result")
                if not isinstance(result, dict) or "name" not in result:
                    reasons.add(f"no substitution for {name}")
                    break

                selected.append(str(choice.get("id", "")))
                ingredient["name"] = deepcopy(result["name"])
                ingredient["quantity"] = (
                    Fraction(ingredient["quantity"])
                    * Fraction(result.get("quantity_factor", 1))
                )

                for field in ("unit", "preparation"):
                    if field in result:
                        ingredient[field] = deepcopy(result[field])

                _apply_wording(adapted, result.get("wording_changes", []))

                for item in _equipment_values(result, "remove"):
                    equipment.discard(str(item))
                for item in _equipment_values(result, "add"):
                    equipment.add(str(item))

                group.extend(_additional_ingredients(result))

                if "yield" in result:
                    adapted["yield"] = Fraction(result["yield"])
                if "yield_factor" in result:
                    adapted["yield"] = (
                        Fraction(adapted["yield"])
                        * Fraction(result["yield_factor"])
                    )

                for warning in _list(choice.get("warnings")):
                    warnings.add(str(warning))
                for warning in _list(result.get("warnings")):
                    warnings.add(str(warning))

            index += 1

    target_yield = Fraction(request["target_yield"])
    current_yield = Fraction(adapted["yield"])
    scale = target_yield / current_yield

    for group in groups:
        for ingredient in group:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    adapted["yield"] = target_yield
    adapted["equipment"] = sorted(equipment)

    available = _normalized(request.get("available_equipment", []))
    for item in equipment:
        if item.casefold() not in available:
            reasons.add(f"equipment {item} unavailable")

    return {
        "possible": not reasons,
        "recipe": adapted if not reasons else None,
        "choices": selected,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }
