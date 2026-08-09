from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable


@dataclass
class _Option:
    value: Any
    choices: list[str]
    wording: list[tuple[str, str]]
    equipment_ops: list[tuple[str, str]]


def _normal(value: Any) -> str:
    return str(value).casefold()


def _catalog_index(catalog: Iterable[dict]) -> dict[str, list[tuple[int, dict]]]:
    indexed: dict[str, list[tuple[int, dict]]] = {}
    for position, choice in enumerate(catalog):
        indexed.setdefault(_normal(choice.get("for", "")), []).append(
            (position, choice)
        )
    for choices in indexed.values():
        choices.sort(key=lambda item: (item[1].get("priority", 0), item[0]))
    return indexed


def _equipment_ops(result: dict) -> list[tuple[str, str]]:
    additions = list(result.get("equipment_additions", result.get("add_equipment", [])))
    removals = list(result.get("equipment_removals", result.get("remove_equipment", [])))

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions.extend(equipment.get("additions", equipment.get("add", [])))
        removals.extend(equipment.get("removals", equipment.get("remove", [])))

    return [("remove", str(name)) for name in removals] + [
        ("add", str(name)) for name in additions
    ]


def _wording_changes(result: dict) -> list[tuple[str, str]]:
    changes: list[tuple[str, str]] = []
    for change in result.get("wording_changes", []):
        if isinstance(change, dict) and "old" in change and "new" in change:
            changes.append((str(change["old"]), str(change["new"])))
    return changes


def _additional_ingredients(result: dict) -> list[dict]:
    additions = result.get("additional_ingredients", [])
    if isinstance(additions, dict):
        return [additions]
    return list(additions)


def _resolve_ingredient(
    ingredient: dict,
    excluded: set[str],
    choices_by_name: dict[str, list[tuple[int, dict]]],
    scale: Fraction,
    trail: tuple[str, ...] = (),
) -> tuple[list[_Option], set[str], list[str]]:
    current = deepcopy(ingredient)
    name = str(current.get("name", ""))
    key = _normal(name)

    if key not in excluded:
        return [_Option(current, [], [], [])], set(), []

    if key in trail:
        return [], {f"substitution cycle involving {name}"}, []

    candidates = choices_by_name.get(key, [])
    if not candidates:
        return [], {f"no substitution for {name}"}, []

    options: list[_Option] = []
    reasons: set[str] = set()
    considered: list[str] = []

    for _, catalog_choice in candidates:
        choice_id = str(catalog_choice["id"])
        considered.append(choice_id)
        result = catalog_choice.get("result", {})

        replacement = deepcopy(current)
        replacement["name"] = result.get("name", replacement.get("name"))
        replacement["quantity"] = replacement["quantity"] * Fraction(
            result.get("quantity_factor", 1)
        )
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        replacement_options, replacement_reasons, replacement_considered = (
            _resolve_ingredient(
                replacement,
                excluded,
                choices_by_name,
                scale,
                trail + (key,),
            )
        )
        reasons.update(replacement_reasons)
        considered.extend(replacement_considered)

        addition_groups: list[list[_Option]] = []
        candidate_failed = not replacement_options

        for addition_source in _additional_ingredients(result):
            addition = deepcopy(addition_source)
            addition["quantity"] = addition["quantity"] * scale
            addition_options, addition_reasons, addition_considered = (
                _resolve_ingredient(
                    addition,
                    excluded,
                    choices_by_name,
                    scale,
                    trail + (key,),
                )
            )
            reasons.update(addition_reasons)
            considered.extend(addition_considered)
            if not addition_options:
                candidate_failed = True
            addition_groups.append(addition_options)

        if candidate_failed:
            continue

        partials = [
            _Option(
                [replacement_option.value],
                [choice_id] + replacement_option.choices,
                _wording_changes(result) + replacement_option.wording,
                _equipment_ops(result) + replacement_option.equipment_ops,
            )
            for replacement_option in replacement_options
        ]

        for group in addition_groups:
            combined: list[_Option] = []
            for partial in partials:
                for addition_option in group:
                    combined.append(
                        _Option(
                            partial.value + [addition_option.value],
                            partial.choices + addition_option.choices,
                            partial.wording + addition_option.wording,
                            partial.equipment_ops + addition_option.equipment_ops,
                        )
                    )
            partials = combined

        options.extend(partials)

    return options, reasons, considered


def _adapt_ingredient_list(
    ingredients: list[dict],
    excluded: set[str],
    choices_by_name: dict[str, list[tuple[int, dict]]],
    scale: Fraction,
) -> tuple[list[_Option], set[str], list[str]]:
    partials = [_Option([], [], [], [])]
    reasons: set[str] = set()
    considered: list[str] = []

    for ingredient in ingredients:
        ingredient_options, item_reasons, item_considered = _resolve_ingredient(
            ingredient, excluded, choices_by_name, scale
        )
        reasons.update(item_reasons)
        considered.extend(item_considered)

        if not ingredient_options:
            partials = []
            continue

        if not partials:
            continue

        combined: list[_Option] = []
        for partial in partials:
            for option in ingredient_options:
                values = option.value if isinstance(option.value, list) else [option.value]
                combined.append(
                    _Option(
                        partial.value + values,
                        partial.choices + option.choices,
                        partial.wording + option.wording,
                        partial.equipment_ops + option.equipment_ops,
                    )
                )
        partials = combined

    return partials, reasons, considered


def _combine_nodes(groups: list[list[_Option]]) -> list[_Option]:
    partials = [_Option([], [], [], [])]
    for group in groups:
        if not group:
            return []
        combined: list[_Option] = []
        for partial in partials:
            for option in group:
                combined.append(
                    _Option(
                        partial.value + [option.value],
                        partial.choices + option.choices,
                        partial.wording + option.wording,
                        partial.equipment_ops + option.equipment_ops,
                    )
                )
        partials = combined
    return partials


def _adapt_structure(
    node: Any,
    excluded: set[str],
    choices_by_name: dict[str, list[tuple[int, dict]]],
    scale: Fraction,
) -> tuple[list[_Option], set[str], list[str]]:
    if isinstance(node, list):
        groups: list[list[_Option]] = []
        reasons: set[str] = set()
        considered: list[str] = []
        for item in node:
            options, item_reasons, item_considered = _adapt_structure(
                item, excluded, choices_by_name, scale
            )
            groups.append(options)
            reasons.update(item_reasons)
            considered.extend(item_considered)
        return _combine_nodes(groups), reasons, considered

    if not isinstance(node, dict):
        return [_Option(deepcopy(node), [], [], [])], set(), []

    base = deepcopy(node)
    reasons: set[str] = set()
    considered: list[str] = []
    ingredient_options = [_Option(base.get("ingredients"), [], [], [])]
    component_options = [_Option(base.get("components"), [], [], [])]

    if isinstance(base.get("ingredients"), list):
        ingredient_options, item_reasons, item_considered = _adapt_ingredient_list(
            base["ingredients"], excluded, choices_by_name, scale
        )
        reasons.update(item_reasons)
        considered.extend(item_considered)

    if "components" in base:
        component_options, item_reasons, item_considered = _adapt_structure(
            base["components"], excluded, choices_by_name, scale
        )
        reasons.update(item_reasons)
        considered.extend(item_considered)

    if not ingredient_options or not component_options:
        return [], reasons, considered

    options: list[_Option] = []
    for ingredient_option in ingredient_options:
        for component_option in component_options:
            value = deepcopy(base)
            if "ingredients" in base:
                value["ingredients"] = ingredient_option.value
            if "components" in base:
                value["components"] = component_option.value
            options.append(
                _Option(
                    value,
                    ingredient_option.choices + component_option.choices,
                    ingredient_option.wording + component_option.wording,
                    ingredient_option.equipment_ops + component_option.equipment_ops,
                )
            )
    return options, reasons, considered


def _scale_structure(node: Any, factor: Fraction, root: bool = False) -> Any:
    value = deepcopy(node)
    if isinstance(value, list):
        return [_scale_structure(item, factor) for item in value]
    if not isinstance(value, dict):
        return value

    if not root and isinstance(value.get("yield"), Fraction):
        value["yield"] *= factor

    if isinstance(value.get("ingredients"), list):
        scaled = []
        for ingredient in value["ingredients"]:
            item = deepcopy(ingredient)
            item["quantity"] = item["quantity"] * factor
            scaled.append(item)
        value["ingredients"] = scaled

    if "components" in value:
        value["components"] = _scale_structure(value["components"], factor)
    return value


def _apply_wording(node: Any, changes: list[tuple[str, str]]) -> Any:
    value = deepcopy(node)
    if isinstance(value, list):
        return [_apply_wording(item, changes) for item in value]
    if not isinstance(value, dict):
        return value

    instructions = value.get("instructions")
    if isinstance(instructions, list):
        rewritten = []
        for instruction in instructions:
            text = instruction
            if isinstance(text, str):
                for old, new in changes:
                    text = text.replace(old, new)
            rewritten.append(text)
        value["instructions"] = rewritten

    if "components" in value:
        value["components"] = _apply_wording(value["components"], changes)
    return value


def _apply_equipment(recipe: dict, operations: list[tuple[str, str]]) -> dict:
    value = deepcopy(recipe)
    equipment = set(value.get("equipment", []))
    for operation, name in operations:
        if operation == "remove":
            equipment.discard(name)
        else:
            equipment.add(name)
    value["equipment"] = sorted(equipment)
    return value


def _sort_nested_equipment(node: Any) -> Any:
    value = deepcopy(node)
    if isinstance(value, list):
        return [_sort_nested_equipment(item) for item in value]
    if not isinstance(value, dict):
        return value
    if isinstance(value.get("equipment"), list):
        value["equipment"] = sorted(set(value["equipment"]))
    if "components" in value:
        value["components"] = _sort_nested_equipment(value["components"])
    return value


def _all_equipment(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, list):
        for item in node:
            found.update(_all_equipment(item))
    elif isinstance(node, dict):
        if isinstance(node.get("equipment"), list):
            found.update(str(item) for item in node["equipment"])
        if "components" in node:
            found.update(_all_equipment(node["components"]))
    return found


def _unique(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def adapt(recipe: dict, request: dict, catalog: list[dict]) -> dict:
    original_yield = recipe["yield"]
    target_yield = request["target_yield"]
    factor = Fraction(target_yield, original_yield)
    excluded = {_normal(name) for name in request.get("excluded", [])}
    available = {str(name) for name in request.get("available_equipment", [])}
    choices_by_name = _catalog_index(catalog)

    scaled = _scale_structure(recipe, factor, root=True)
    scaled["yield"] = target_yield

    options, substitution_reasons, considered = _adapt_structure(
        scaled, excluded, choices_by_name, factor
    )

    equipment_reasons: set[str] = set()
    for option in options:
        candidate = _apply_wording(option.value, option.wording)
        candidate = _apply_equipment(candidate, option.equipment_ops)
        candidate = _sort_nested_equipment(candidate)
        unavailable = _all_equipment(candidate) - available
        if not unavailable:
            return {
                "possible": True,
                "recipe": candidate,
                "choices": option.choices,
                "warnings": [],
                "reasons": [],
            }
        equipment_reasons.update(
            f"equipment {name} unavailable" for name in unavailable
        )

    if not options:
        base_unavailable = _all_equipment(_sort_nested_equipment(scaled)) - available
        equipment_reasons.update(
            f"equipment {name} unavailable" for name in base_unavailable
        )

    return {
        "possible": False,
        "recipe": None,
        "choices": _unique(considered),
        "warnings": [],
        "reasons": sorted(substitution_reasons | equipment_reasons),
    }


def print_original(recipe: dict) -> str:
    return recipe["authored_text"]
