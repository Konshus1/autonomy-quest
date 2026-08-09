from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterable


def _key(value: Any) -> str:
    return str(value).strip().casefold()


def _containers(value: Any) -> Iterable[dict]:
    """Yield recipe/component dictionaries containing adaptable fields."""
    if isinstance(value, dict):
        if any(key in value for key in ("ingredients", "equipment", "instructions")):
            yield value

        components = value.get("components", [])
        if isinstance(components, dict):
            components = components.values()

        if isinstance(components, Iterable) and not isinstance(components, (str, bytes)):
            for component in components:
                yield from _containers(component)

    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _containers(item)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def print_original(recipe: dict) -> str:
    """Return the recipe exactly as authored."""
    return recipe["authored_text"]


def adapt(recipe: dict, request: dict, catalog: list[dict]) -> dict:
    """Adapt a recipe without mutating any supplied object."""
    work = deepcopy(recipe)
    excluded = {_key(name) for name in request.get("excluded", [])}
    available = {_key(name) for name in request.get("available_equipment", [])}

    ordered_catalog = sorted(
        enumerate(catalog),
        key=lambda entry: (entry[1].get("priority", 0), entry[0]),
    )

    choices: list[Any] = []
    warnings: list[str] = []
    reasons: set[str] = set()
    effective_yield = [Fraction(work["yield"])]

    def candidates(ingredient: dict) -> list[dict]:
        names = {
            _key(ingredient.get("name", "")),
            _key(ingredient.get("id", "")),
        }
        return [
            choice
            for _, choice in ordered_catalog
            if _key(choice.get("for", "")) in names
        ]

    def rewrite_text(old: str, new: str) -> None:
        for container in _containers(work):
            instructions = container.get("instructions")
            if isinstance(instructions, list):
                container["instructions"] = [
                    line.replace(old, new) if isinstance(line, str) else line
                    for line in instructions
                ]

            ingredients = container.get("ingredients")
            if isinstance(ingredients, list):
                for ingredient in ingredients:
                    preparation = (
                        ingredient.get("preparation")
                        if isinstance(ingredient, dict)
                        else None
                    )
                    if isinstance(preparation, str):
                        ingredient["preparation"] = preparation.replace(old, new)

    def change_equipment(container: dict, result: dict) -> None:
        equipment = container.setdefault("equipment", [])

        removals = result.get(
            "equipment_removals",
            result.get("remove_equipment", []),
        )
        removal_keys = {_key(item) for item in _as_list(removals)}
        if removal_keys:
            equipment[:] = [
                item for item in equipment if _key(item) not in removal_keys
            ]

        additions = result.get(
            "equipment_additions",
            result.get("add_equipment", []),
        )
        present = {_key(item) for item in equipment}
        for item in _as_list(additions):
            if _key(item) not in present:
                equipment.append(item)
                present.add(_key(item))

    def collect_warnings(choice: dict, result: dict) -> None:
        for source in (choice.get("warnings"), result.get("warnings")):
            if isinstance(source, str):
                warnings.append(source)
            elif source:
                warnings.extend(str(item) for item in source)

    def resolve(
        ingredient: dict,
        container: dict,
        path: tuple[str, ...],
    ) -> list[dict]:
        current_name = str(ingredient.get("name", ""))
        current_key = _key(current_name)

        if current_key not in excluded:
            return [ingredient]

        if current_key in path:
            reasons.add(f"substitution cycle involving {current_name}")
            return [ingredient]

        options = candidates(ingredient)
        if not options:
            reasons.add(f"no substitution for {current_name}")
            return [ingredient]

        choice = options[0]
        result = choice.get("result") or {}
        choices.append(choice.get("id"))
        collect_warnings(choice, result)

        replacement = deepcopy(ingredient)
        old_name = str(replacement.get("name", ""))
        replacement["name"] = result.get("name", replacement.get("name"))
        replacement["quantity"] = (
            Fraction(replacement["quantity"])
            * Fraction(result.get("quantity_factor", 1))
        )

        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        if result.get("replace_name_in_instructions"):
            rewrite_text(old_name, str(replacement["name"]))

        for change in result.get("wording_changes", []) or []:
            rewrite_text(str(change["old"]), str(change["new"]))

        change_equipment(container, result)

        # These extensions support substitutions that explicitly alter yield.
        if "yield_factor" in result:
            effective_yield[0] *= Fraction(result["yield_factor"])
        if "yield" in result:
            effective_yield[0] = Fraction(result["yield"])

        next_path = path + (current_key,)
        resolved = resolve(replacement, container, next_path)

        for additional in result.get("additional_ingredients", []) or []:
            resolved.extend(resolve(deepcopy(additional), container, next_path))

        return resolved

    # Snapshot containers because ingredient lists are replaced during traversal.
    for container in list(_containers(work)):
        ingredients = container.get("ingredients")
        if not isinstance(ingredients, list):
            continue

        adapted_ingredients: list = []
        for ingredient in list(ingredients):
            if isinstance(ingredient, dict):
                adapted_ingredients.extend(resolve(ingredient, container, ()))
            else:
                adapted_ingredients.append(ingredient)
        container["ingredients"] = adapted_ingredients

    target_yield = Fraction(request["target_yield"])
    if effective_yield[0] == 0:
        raise ValueError("recipe yield must not be zero")

    scale = target_yield / effective_yield[0]

    for container in _containers(work):
        ingredients = container.get("ingredients")
        if isinstance(ingredients, list):
            for ingredient in ingredients:
                if isinstance(ingredient, dict) and "quantity" in ingredient:
                    ingredient["quantity"] = (
                        Fraction(ingredient["quantity"]) * scale
                    )

        equipment = container.get("equipment")
        if isinstance(equipment, list):
            equipment.sort(key=str)
            for name in equipment:
                if _key(name) not in available:
                    reasons.add(f"equipment {name} unavailable")

    work["yield"] = target_yield

    # Ensure exclusions introduced by substitutions or nested components cannot
    # escape final validation.
    for container in _containers(work):
        for ingredient in container.get("ingredients", []) or []:
            if not isinstance(ingredient, dict):
                continue

            name = str(ingredient.get("name", ""))
            if _key(name) not in excluded:
                continue

            if (
                f"no substitution for {name}" not in reasons
                and f"substitution cycle involving {name}" not in reasons
            ):
                reasons.add(f"no substitution for {name}")

    sorted_reasons = sorted(reasons)
    return {
        "possible": not sorted_reasons,
        "recipe": work if not sorted_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted_reasons,
    }
