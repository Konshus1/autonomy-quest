"""Dependency-free recipe adaptation using exact rational quantities."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterable


def print_original(recipe: dict) -> str:
    """Return the recipe exactly as authored."""

    return recipe["authored_text"]


def _key(value: Any) -> str:
    return str(value).casefold()


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _sections(value: Any) -> Iterable[dict]:
    """Yield the root recipe and recipe-like components in authored order."""

    if not isinstance(value, dict):
        return

    yield value
    components = value.get("components", [])
    if isinstance(components, dict):
        components = components.values()

    if isinstance(components, (list, tuple)):
        for component in components:
            yield from _sections(component)


def _equipment_effect(result: dict, action: str) -> list:
    if action == "add":
        names = ("equipment_additions", "add_equipment", "equipment_add")
    else:
        names = ("equipment_removals", "remove_equipment", "equipment_remove")

    values = []
    for name in names:
        values.extend(_as_list(result.get(name)))

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        values.extend(_as_list(equipment.get(action)))

    return values


def adapt(recipe: dict, request: dict, catalog: list) -> dict:
    """Return an adapted recipe without mutating any supplied value."""

    work = deepcopy(recipe)
    excluded = {_key(name) for name in request.get("excluded", [])}
    available = {
        _key(name) for name in request.get("available_equipment", [])
    }

    catalog_index = {}
    for position, choice in enumerate(catalog):
        catalog_index.setdefault(_key(choice.get("for", "")), []).append(
            (choice.get("priority", 0), position, choice)
        )

    for alternatives in catalog_index.values():
        alternatives.sort(key=lambda item: (item[0], item[1]))

    sections = list(_sections(work))
    root_equipment = work.setdefault("equipment", [])
    if not isinstance(root_equipment, list):
        root_equipment = list(root_equipment)
        work["equipment"] = root_equipment

    # Each queue entry is (ingredient, containing list, substitution ancestry).
    queue = []
    for section in sections:
        ingredients = section.get("ingredients", [])
        if isinstance(ingredients, list):
            for ingredient in ingredients:
                if isinstance(ingredient, dict):
                    queue.append(
                        (
                            ingredient,
                            ingredients,
                            [_key(ingredient.get("name", ""))],
                        )
                    )

    selected_choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    blocked = set()
    effective_yield = Fraction(work["yield"])
    cursor = 0

    while cursor < len(queue):
        ingredient, container, ancestry = queue[cursor]
        cursor += 1

        marker = id(ingredient)
        name = ingredient.get("name", "")
        name_key = _key(name)

        if marker in blocked or name_key not in excluded:
            continue

        alternatives = catalog_index.get(name_key, [])
        if not alternatives:
            reasons.add(f"no substitution for {name}")
            blocked.add(marker)
            continue

        choice = alternatives[0][2]
        selected_choices.append(choice.get("id"))
        result = choice.get("result") or {}

        replacement = result.get("name", name)
        replacement_key = _key(replacement)
        ingredient["name"] = replacement
        ingredient["quantity"] = (
            Fraction(ingredient["quantity"])
            * Fraction(result.get("quantity_factor", 1))
        )

        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        for change in result.get("wording_changes", []):
            if (
                isinstance(change, dict)
                and "old" in change
                and "new" in change
            ):
                wording_changes.append(
                    (str(change["old"]), str(change["new"]))
                )

        removals = {
            _key(item) for item in _equipment_effect(result, "remove")
        }
        if removals:
            for section in sections:
                equipment = section.get("equipment")
                if isinstance(equipment, list):
                    equipment[:] = [
                        item for item in equipment if _key(item) not in removals
                    ]

        for item in _equipment_effect(result, "add"):
            existing = {_key(current) for current in root_equipment}
            if _key(item) not in existing:
                root_equipment.append(item)

        for source in (choice, result):
            warnings.update(
                str(item) for item in _as_list(source.get("warnings"))
            )
            if "warning" in source:
                warnings.add(str(source["warning"]))

        if "yield" in result:
            effective_yield = Fraction(result["yield"])
        if "yield_factor" in result:
            effective_yield *= Fraction(result["yield_factor"])

        next_ancestry = ancestry + [replacement_key]
        if replacement_key in ancestry:
            reasons.add(f"substitution cycle involving {replacement}")
            blocked.add(marker)
        else:
            # The replacement may itself be excluded.
            queue.append((ingredient, container, next_ancestry))

        additions = result.get("additional_ingredients", [])
        if isinstance(additions, dict):
            additions = [additions]

        for number, supplied in enumerate(additions):
            if not isinstance(supplied, dict):
                continue

            addition = deepcopy(supplied)
            addition.setdefault(
                "id",
                f"{choice.get('id', 'substitution')}:additional:{number}",
            )
            addition.setdefault("quantity", Fraction(0))
            addition["quantity"] = Fraction(addition["quantity"])
            addition.setdefault("unit", "")
            addition.setdefault("preparation", "")
            container.append(addition)

            added_name = addition.get("name", "")
            added_key = _key(added_name)
            added_ancestry = next_ancestry + [added_key]

            if added_key in next_ancestry and added_key in excluded:
                reasons.add(f"substitution cycle involving {added_name}")
                blocked.add(id(addition))

            queue.append((addition, container, added_ancestry))

    # Apply changes after all substitutions so later changes can rewrite text
    # produced by earlier ones.
    for section in sections:
        instructions = section.get("instructions")
        if isinstance(instructions, list):
            rewritten = []
            for instruction in instructions:
                text = instruction
                for old, new in wording_changes:
                    text = text.replace(old, new)
                rewritten.append(text)
            section["instructions"] = rewritten

    # Normalize and validate all recipe-like equipment lists.
    for section in sections:
        equipment = section.get("equipment")
        if not isinstance(equipment, list):
            continue

        unique = {}
        for item in equipment:
            unique.setdefault(_key(item), item)

        section["equipment"] = sorted(unique.values(), key=str)
        for item in section["equipment"]:
            if _key(item) not in available:
                reasons.add(f"equipment {item} unavailable")

    if not reasons:
        target_yield = Fraction(request["target_yield"])
        scale = target_yield / effective_yield
        scaled = set()

        for ingredient, _, _ in queue:
            marker = id(ingredient)
            if marker in scaled:
                continue
            scaled.add(marker)
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"]) * scale
            )

        work["yield"] = target_yield

    return {
        "possible": not reasons,
        "recipe": work if not reasons else None,
        "choices": selected_choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }
