from __future__ import annotations

import copy
import re
from fractions import Fraction
from typing import Any, Iterable


def print_original(recipe: dict) -> str:
    """Return the recipe's original authored text without modification."""
    return recipe["authored_text"]


def _list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _normalized(value: Any) -> str:
    return str(value).strip().casefold()


def _component_dicts(value: Any) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in _component_dicts(value.get("components", [])):
            yield child
    elif isinstance(value, list):
        for item in value:
            yield from _component_dicts(item)


def _recipe_sections(recipe: dict) -> Iterable[dict]:
    yield recipe
    yield from _component_dicts(recipe.get("components", []))


def _first_present(mapping: dict, names: tuple[str, ...], default: Any) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


def _catalog_choices(catalog: Any) -> list[dict]:
    if isinstance(catalog, dict):
        values = catalog.get("choices", [])
    else:
        values = catalog
    return list(values or [])


def _choice_matches(choice: dict, ingredient: dict) -> bool:
    targets = _list(choice.get("for"))
    name = _normalized(ingredient.get("name", ""))
    ingredient_id = _normalized(ingredient.get("id", ""))
    return any(
        _normalized(target) in {name, ingredient_id}
        for target in targets
    )


def _replace_ingredient_name(text: str, old: str, new: str) -> str:
    if not old or old == new:
        return text
    pattern = re.compile(r"(?<!\w)" + re.escape(old) + r"(?!\w)", re.IGNORECASE)
    return pattern.sub(lambda _match: new, text)


def adapt(recipe: dict, request: dict, catalog: Any) -> dict:
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = copy.deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    original_yield = Fraction(adapted["yield"])
    excluded = {_normalized(name) for name in request.get("excluded", [])}
    available_equipment = set(request.get("available_equipment", []))

    indexed_catalog = list(enumerate(_catalog_choices(catalog)))
    indexed_catalog.sort(
        key=lambda entry: (entry[1].get("priority", float("inf")), entry[0])
    )

    equipment = set(adapted.get("equipment", []))
    choices: list[Any] = []
    warnings: list[str] = []
    reasons: set[str] = set()
    wording_changes: list[tuple[str, str, bool]] = []
    effective_yield = original_yield

    def add_warnings(source: dict) -> None:
        for key in ("warning", "warnings"):
            if key in source:
                warnings.extend(str(item) for item in _list(source[key]))

    def resolve(ingredient: dict, path: tuple[str, ...]) -> list[dict]:
        nonlocal effective_yield

        current = copy.deepcopy(ingredient)
        name = str(current.get("name", ""))
        normalized_name = _normalized(name)

        if normalized_name not in excluded:
            return [current]

        if normalized_name in path:
            reasons.add(f"substitution cycle involving {name}")
            return []

        matches = [
            choice
            for _index, choice in indexed_catalog
            if _choice_matches(choice, current)
        ]
        if not matches:
            reasons.add(f"no substitution for {name}")
            return []

        choice = matches[0]
        result = choice.get("result", {})
        choices.append(choice.get("id"))
        add_warnings(choice)
        add_warnings(result)

        replacement = copy.deepcopy(current)
        replacement_name = str(result.get("name", name))
        replacement["name"] = replacement_name
        replacement["quantity"] = Fraction(replacement["quantity"]) * Fraction(
            result.get("quantity_factor", 1)
        )

        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        wording_changes.append((name, replacement_name, True))
        for change in result.get("wording_changes", []):
            wording_changes.append((str(change["old"]), str(change["new"]), False))

        removals = _first_present(
            result,
            ("equipment_removals", "equipment_remove", "remove_equipment"),
            [],
        )
        additions = _first_present(
            result,
            ("equipment_additions", "equipment_add", "add_equipment"),
            [],
        )
        equipment.difference_update(_list(removals))
        equipment.update(_list(additions))

        if "yield" in result:
            effective_yield = Fraction(result["yield"])
        if "yield_factor" in result:
            effective_yield *= Fraction(result["yield_factor"])
        if "yield_change" in result:
            effective_yield *= Fraction(result["yield_change"])

        next_path = path + (normalized_name,)
        resolved = resolve(replacement, next_path)

        for index, additional in enumerate(result.get("additional_ingredients", [])):
            extra = copy.deepcopy(additional)
            extra.setdefault("id", f"{choice.get('id', 'substitution')}:{index + 1}")
            extra.setdefault("unit", "")
            extra.setdefault("preparation", "")
            resolved.extend(resolve(extra, next_path))

        return resolved

    for section in _recipe_sections(adapted):
        if "ingredients" not in section:
            continue
        resolved_ingredients: list[dict] = []
        for ingredient in section.get("ingredients", []):
            resolved_ingredients.extend(resolve(ingredient, ()))
        section["ingredients"] = resolved_ingredients

    if effective_yield == 0:
        raise ValueError("recipe yield must not be zero")

    scale = target_yield / effective_yield
    for section in _recipe_sections(adapted):
        for ingredient in section.get("ingredients", []):
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    def edit_instructions(section: dict) -> None:
        if "instructions" in section:
            edited: list[str] = []
            for authored in section.get("instructions", []):
                text = authored
                for old, new, is_ingredient_name in wording_changes:
                    if is_ingredient_name:
                        text = _replace_ingredient_name(text, old, new)
                    else:
                        text = text.replace(old, new)
                edited.append(text)
            section["instructions"] = edited

    for section in _recipe_sections(adapted):
        edit_instructions(section)

    adapted["yield"] = target_yield
    adapted["equipment"] = sorted(equipment)

    for item in equipment:
        if item not in available_equipment:
            reasons.add(f"equipment {item} unavailable")

    return {
        "possible": not reasons,
        "recipe": adapted if not reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }
