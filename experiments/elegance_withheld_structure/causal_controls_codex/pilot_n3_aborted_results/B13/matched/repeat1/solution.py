"""Recipe adaptation using only the Python standard library."""

from __future__ import annotations

import re
from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterable


def _key(value: Any) -> str:
    return str(value).strip().casefold()


def _fraction(value: Any) -> Fraction:
    return value if isinstance(value, Fraction) else Fraction(value)


def _walk_dicts(value: Any) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _lists_named(recipe: dict, field: str) -> list[list]:
    return [
        node[field]
        for node in _walk_dicts(recipe)
        if isinstance(node.get(field), list)
    ]


def _ingredient_lists(recipe: dict) -> list[list]:
    return _lists_named(recipe, "ingredients")


def _instruction_lists(recipe: dict) -> list[list]:
    return _lists_named(recipe, "instructions")


def _equipment_lists(recipe: dict) -> list[list]:
    return _lists_named(recipe, "equipment")


def _replace_text(text: str, old: str, new: str) -> str:
    if not old:
        return text

    prefix = r"(?<!\w)" if old[0].isalnum() else ""
    suffix = r"(?!\w)" if old[-1].isalnum() else ""
    pattern = re.compile(prefix + re.escape(old) + suffix, re.IGNORECASE)

    def replacement(match: re.Match) -> str:
        found = match.group(0)
        if found.isupper():
            return new.upper()
        if found[:1].isupper():
            return new[:1].upper() + new[1:]
        return new

    return pattern.sub(replacement, text)


def _rewrite_instructions(recipe: dict, old: str, new: str) -> None:
    for instructions in _instruction_lists(recipe):
        for index, statement in enumerate(instructions):
            if isinstance(statement, str):
                instructions[index] = _replace_text(statement, old, new)


def _rewrite_preparations(recipe: dict, old: str, new: str) -> None:
    for ingredients in _ingredient_lists(recipe):
        for ingredient in ingredients:
            preparation = ingredient.get("preparation")
            if isinstance(preparation, str):
                ingredient["preparation"] = _replace_text(
                    preparation, old, new
                )


def _scale_all_quantities(recipe: dict, factor: Fraction) -> None:
    for ingredients in _ingredient_lists(recipe):
        for ingredient in ingredients:
            ingredient["quantity"] = (
                _fraction(ingredient["quantity"]) * factor
            )


def _equipment_effects(result: dict) -> tuple[list, list]:
    nested = result.get("equipment", {})
    if not isinstance(nested, dict):
        nested = {}

    removals = result.get(
        "equipment_remove",
        result.get(
            "equipment_removals",
            result.get(
                "remove_equipment",
                nested.get("remove", nested.get("removals", [])),
            ),
        ),
    )
    additions = result.get(
        "equipment_add",
        result.get(
            "equipment_additions",
            result.get(
                "add_equipment",
                nested.get("add", nested.get("additions", [])),
            ),
        ),
    )
    return list(removals or []), list(additions or [])


def _change_equipment(
    recipe: dict, removals: Iterable[Any], additions: Iterable[Any]
) -> None:
    removal_keys = {_key(item) for item in removals}
    equipment_lists = _equipment_lists(recipe)

    if not equipment_lists:
        recipe["equipment"] = []
        equipment_lists = [recipe["equipment"]]

    for equipment in equipment_lists:
        equipment[:] = [
            item for item in equipment if _key(item) not in removal_keys
        ]

    root_equipment = recipe.setdefault("equipment", [])
    present = {_key(item) for item in root_equipment}
    for item in additions:
        if _key(item) not in present:
            root_equipment.append(deepcopy(item))
            present.add(_key(item))


def _catalog_index(catalog: list[dict]) -> dict[str, list[dict]]:
    indexed: dict[str, list[tuple[Any, int, dict]]] = {}
    for order, choice in enumerate(catalog):
        indexed.setdefault(_key(choice.get("for", "")), []).append(
            (choice.get("priority", 0), order, choice)
        )

    return {
        name: [
            choice
            for _, _, choice in sorted(
                entries, key=lambda entry: (entry[0], entry[1])
            )
        ]
        for name, entries in indexed.items()
    }


def _make_additional(spec: dict, scale: Fraction, serial: int) -> dict:
    ingredient = deepcopy(spec)
    ingredient.setdefault("id", f"additional-{serial}")
    ingredient.setdefault("unit", "")
    ingredient.setdefault("preparation", "")
    ingredient["quantity"] = (
        _fraction(ingredient.get("quantity", 0)) * scale
    )
    return ingredient


def _record_warnings(result: dict, warnings: set[str]) -> None:
    warning = result.get("warning")
    if warning is not None:
        warnings.add(str(warning))

    listed = result.get("warnings", [])
    if isinstance(listed, (str, bytes)):
        warnings.add(str(listed))
    else:
        warnings.update(str(item) for item in listed)


def adapt(recipe: dict, request: dict, catalog: list[dict]) -> dict:
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    working = deepcopy(recipe)
    excluded = {_key(item) for item in request.get("excluded", [])}
    available = {
        _key(item) for item in request.get("available_equipment", [])
    }
    choices_by_name = _catalog_index(catalog)

    choices: list[Any] = []
    warnings: set[str] = set()
    reasons: set[str] = set()

    original_yield = _fraction(recipe["yield"])
    target_yield = _fraction(request["target_yield"])
    effective_yield = original_yield
    quantity_scale = target_yield / effective_yield

    working["yield"] = target_yield
    _scale_all_quantities(working, quantity_scale)

    pending: list[tuple[list, dict, tuple[str, ...]]] = []
    for ingredients in _ingredient_lists(working):
        for ingredient in ingredients:
            pending.append((ingredients, ingredient, ()))

    additional_serial = 0

    while pending:
        owner, ingredient, ancestry = pending.pop(0)
        name = str(ingredient.get("name", ""))
        name_key = _key(name)

        if name_key not in excluded:
            continue

        if name_key in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            continue

        candidates = choices_by_name.get(name_key, [])
        if not candidates:
            reasons.add(f"no substitution for {name}")
            continue

        choice = candidates[0]
        choices.append(choice.get("id"))
        result = choice.get("result") or {}
        _record_warnings(result, warnings)

        old_name = name
        replacement_name = str(result.get("name", old_name))
        ingredient["name"] = replacement_name

        if "quantity_factor" in result:
            ingredient["quantity"] *= _fraction(
                result["quantity_factor"]
            )
        if "unit" in result:
            ingredient["unit"] = deepcopy(result["unit"])
        if "preparation" in result:
            ingredient["preparation"] = deepcopy(result["preparation"])

        _rewrite_instructions(working, old_name, replacement_name)
        _rewrite_preparations(working, old_name, replacement_name)

        for change in result.get("wording_changes", []):
            _rewrite_instructions(
                working,
                str(change.get("old", "")),
                str(change.get("new", "")),
            )

        removals, additions = _equipment_effects(result)
        _change_equipment(working, removals, additions)

        new_effective_yield = effective_yield
        if "yield" in result:
            new_effective_yield = _fraction(result["yield"])
        if "yield_factor" in result:
            new_effective_yield *= _fraction(result["yield_factor"])

        if new_effective_yield != effective_yield:
            _scale_all_quantities(
                working, effective_yield / new_effective_yield
            )
            effective_yield = new_effective_yield
            quantity_scale = target_yield / effective_yield

        next_ancestry = ancestry + (name_key,)
        new_pending: list[tuple[list, dict, tuple[str, ...]]] = [
            (owner, ingredient, next_ancestry)
        ]

        for extra_spec in result.get("additional_ingredients", []):
            additional_serial += 1
            extra = _make_additional(
                extra_spec, quantity_scale, additional_serial
            )
            owner.append(extra)
            new_pending.append((owner, extra, next_ancestry))

        # Resolve the chain and its additions before moving to the next original
        # ingredient, making the choice order deterministic and easy to audit.
        pending[0:0] = new_pending

    for equipment in _equipment_lists(working):
        unique: dict[str, Any] = {}
        for item in equipment:
            unique.setdefault(_key(item), item)
        equipment[:] = sorted(
            unique.values(), key=lambda item: (_key(item), str(item))
        )

        for item in equipment:
            if _key(item) not in available:
                reasons.add(f"equipment {item} unavailable")

    possible = not reasons
    return {
        "possible": possible,
        "recipe": working if possible else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }


def print_original(recipe: dict) -> str:
    """Return authored_text byte-for-byte as a Python string."""
    return recipe["authored_text"]
