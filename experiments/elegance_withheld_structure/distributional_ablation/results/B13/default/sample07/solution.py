from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable


@dataclass(frozen=True)
class _Effects:
    choices: tuple[Any, ...] = ()
    warnings: tuple[str, ...] = ()
    wording: tuple[tuple[str, str], ...] = ()
    equipment_ops: tuple[tuple[str, str], ...] = ()

    def then(self, other: "_Effects") -> "_Effects":
        return _Effects(
            self.choices + other.choices,
            self.warnings + other.warnings,
            self.wording + other.wording,
            self.equipment_ops + other.equipment_ops,
        )


@dataclass(frozen=True)
class _Resolution:
    ingredients: tuple[dict[str, Any], ...]
    effects: _Effects = _Effects()


def _norm(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _ordered_unique(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _choice_warnings(choice: dict[str, Any], result: dict[str, Any]) -> tuple[str, ...]:
    warnings: list[str] = []
    for source in (choice, result):
        value = source.get("warnings", source.get("warning", ()))
        if isinstance(value, str):
            warnings.append(value)
        elif value:
            warnings.extend(str(item) for item in value)
    return tuple(warnings)


def _string_list(mapping: dict[str, Any], *keys: str) -> list[str]:
    output: list[str] = []
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str):
            output.append(value)
        elif value:
            output.extend(str(item) for item in value)
    return output


def _choice_effects(
    ingredient: dict[str, Any], choice: dict[str, Any]
) -> _Effects:
    result = choice.get("result") or {}
    wording: list[tuple[str, str]] = []

    old_name = str(ingredient.get("name", ""))
    new_name = str(result.get("name", old_name))
    if old_name and old_name != new_name:
        wording.append((old_name, new_name))

    for change in result.get("wording_changes", ()) or ():
        if isinstance(change, dict):
            old = str(change.get("old", ""))
            new = str(change.get("new", ""))
        else:
            old, new = change
            old, new = str(old), str(new)
        if old:
            wording.append((old, new))

    operations: list[tuple[str, str]] = []
    removals = _string_list(
        result,
        "equipment_removals",
        "equipment_remove",
        "remove_equipment",
    )
    additions = _string_list(
        result,
        "equipment_additions",
        "equipment_add",
        "add_equipment",
    )
    operations.extend(("remove", item) for item in removals)
    operations.extend(("add", item) for item in additions)

    return _Effects(
        choices=(choice.get("id"),),
        warnings=_choice_warnings(choice, result),
        wording=tuple(wording),
        equipment_ops=tuple(operations),
    )


def _matching_choices(
    ingredient: dict[str, Any], catalog: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    name = _norm(ingredient.get("name", ""))
    ingredient_id = _norm(ingredient.get("id", ""))
    ranked: list[tuple[Any, int, dict[str, Any]]] = []

    for index, choice in enumerate(catalog):
        target = _norm(choice.get("for", ""))
        if target and target in {name, ingredient_id}:
            ranked.append((choice.get("priority", 0), index, choice))

    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked]


def _replace_ingredient(
    ingredient: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    replacement = deepcopy(ingredient)
    replacement["name"] = result["name"]
    replacement["quantity"] = replacement["quantity"] * result.get(
        "quantity_factor", Fraction(1, 1)
    )
    if "unit" in result:
        replacement["unit"] = result["unit"]
    if "preparation" in result:
        replacement["preparation"] = result["preparation"]
    return replacement


def _prepare_additional(
    ingredient: dict[str, Any],
    choice: dict[str, Any],
    additional: dict[str, Any],
    index: int,
    scale: Fraction,
) -> dict[str, Any]:
    prepared = deepcopy(additional)
    if "quantity" in prepared:
        prepared["quantity"] = prepared["quantity"] * scale
    if "id" not in prepared:
        prepared["id"] = "{}__{}__{}".format(
            ingredient.get("id", "ingredient"), choice.get("id", "choice"), index
        )
    prepared.setdefault("unit", "")
    prepared.setdefault("preparation", "")
    return prepared


def _resolve_ingredient(
    ingredient: dict[str, Any],
    excluded: set[str],
    catalog: list[dict[str, Any]],
    scale: Fraction,
    lineage: tuple[str, ...] = (),
) -> tuple[list[_Resolution], set[str]]:
    name = str(ingredient.get("name", ""))
    key = _norm(name)

    if key not in excluded:
        return [_Resolution((deepcopy(ingredient),))], set()

    if key in lineage:
        return [], {f"substitution cycle involving {name}"}

    choices = _matching_choices(ingredient, catalog)
    if not choices:
        return [], {f"no substitution for {name}"}

    successful: list[_Resolution] = []
    failed_reasons: set[str] = set()
    next_lineage = lineage + (key,)

    for choice in choices:
        result = choice.get("result") or {}
        if "name" not in result:
            failed_reasons.add(f"no substitution for {name}")
            continue

        replacement = _replace_ingredient(ingredient, result)
        main_resolutions, main_errors = _resolve_ingredient(
            replacement, excluded, catalog, scale, next_lineage
        )
        if not main_resolutions:
            failed_reasons.update(main_errors)
            continue

        prefix = _choice_effects(ingredient, choice)
        branch = [
            _Resolution(item.ingredients, prefix.then(item.effects))
            for item in main_resolutions
        ]

        additions = result.get(
            "additional_ingredients", result.get("additional", ())
        ) or ()

        for index, additional in enumerate(additions):
            prepared = _prepare_additional(
                ingredient, choice, additional, index, scale
            )
            added_resolutions, added_errors = _resolve_ingredient(
                prepared, excluded, catalog, scale, next_lineage
            )
            if not added_resolutions:
                failed_reasons.update(added_errors)
                branch = []
                break

            combined: list[_Resolution] = []
            for existing in branch:
                for added in added_resolutions:
                    combined.append(
                        _Resolution(
                            existing.ingredients + added.ingredients,
                            existing.effects.then(added.effects),
                        )
                    )
            branch = combined

        successful.extend(branch)

    if successful:
        return successful, set()
    return [], failed_reasons


def _ingredient_paths(value: Any, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    paths: list[tuple[Any, ...]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (key,)
            if key == "ingredients" and isinstance(child, list):
                paths.append(child_path)
            else:
                paths.extend(_ingredient_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_ingredient_paths(child, path + (index,)))
    return paths


def _at_path(value: Any, path: tuple[Any, ...]) -> Any:
    current = value
    for part in path:
        current = current[part]
    return current


def _set_path(value: Any, path: tuple[Any, ...], replacement: Any) -> None:
    current = value
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = replacement


def _scale_quantities(recipe: dict[str, Any], scale: Fraction) -> None:
    for path in _ingredient_paths(recipe):
        ingredients = _at_path(recipe, path)
        for ingredient in ingredients:
            ingredient["quantity"] = ingredient["quantity"] * scale


def _replace_instruction_wording(value: Any, changes: tuple[tuple[str, str], ...]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "instructions" and isinstance(child, list):
                replaced: list[Any] = []
                for instruction in child:
                    if not isinstance(instruction, str):
                        replaced.append(instruction)
                        continue
                    text = instruction
                    for old, new in changes:
                        text = text.replace(old, new)
                    replaced.append(text)
                value[key] = replaced
            elif key != "authored_text":
                _replace_instruction_wording(child, changes)
    elif isinstance(value, list):
        for child in value:
            _replace_instruction_wording(child, changes)


def _final_equipment(
    equipment: list[Any],
    operations: tuple[tuple[str, str], ...],
    available: set[str],
) -> tuple[list[str], list[str]]:
    current: dict[str, str] = {}
    for item in equipment:
        current.setdefault(_norm(item), str(item))

    for operation, item in operations:
        key = _norm(item)
        if operation == "remove":
            current.pop(key, None)
        else:
            current.setdefault(key, str(item))

    final = sorted(current.values(), key=lambda item: (item.casefold(), item))
    reasons = [
        f"equipment {item} unavailable"
        for item in final
        if _norm(item) not in available
    ]
    return final, reasons


def _finish_recipe(
    recipe: dict[str, Any],
    effects: _Effects,
    available: set[str],
) -> tuple[dict[str, Any], list[str]]:
    finished = deepcopy(recipe)
    equipment, reasons = _final_equipment(
        finished.get("equipment", []), effects.equipment_ops, available
    )
    finished["equipment"] = equipment
    _replace_instruction_wording(finished, effects.wording)
    return finished, reasons


def adapt(
    recipe: dict[str, Any],
    request: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    working = deepcopy(recipe)
    target_yield = request["target_yield"]
    scale = target_yield / working["yield"]
    working["yield"] = target_yield
    _scale_quantities(working, scale)

    excluded = {_norm(item) for item in request.get("excluded", ())}
    available = {_norm(item) for item in request.get("available_equipment", ())}

    branches: list[tuple[dict[str, Any], _Effects]] = [(working, _Effects())]
    structural_reasons: set[str] = set()

    for path in _ingredient_paths(working):
        expanded: list[tuple[dict[str, Any], _Effects]] = []
        for document, accumulated in branches:
            ingredients = _at_path(document, path)
            group_options: list[tuple[list[dict[str, Any]], _Effects]] = [([], _Effects())]

            for ingredient in ingredients:
                resolutions, errors = _resolve_ingredient(
                    ingredient, excluded, catalog, scale
                )
                if not resolutions:
                    structural_reasons.update(errors)
                    resolutions = [_Resolution((deepcopy(ingredient),))]

                next_options: list[tuple[list[dict[str, Any]], _Effects]] = []
                for prior_ingredients, prior_effects in group_options:
                    for resolution in resolutions:
                        next_options.append(
                            (
                                prior_ingredients + list(resolution.ingredients),
                                prior_effects.then(resolution.effects),
                            )
                        )
                group_options = next_options

            for replacement_group, group_effects in group_options:
                candidate = deepcopy(document)
                _set_path(candidate, path, replacement_group)
                expanded.append((candidate, accumulated.then(group_effects)))
        branches = expanded

    if structural_reasons:
        candidate, effects = branches[0]
        _, equipment_reasons = _finish_recipe(candidate, effects, available)
        reasons = sorted(structural_reasons.union(equipment_reasons))
        return {
            "possible": False,
            "recipe": None,
            "choices": _ordered_unique(effects.choices),
            "warnings": sorted(set(effects.warnings)),
            "reasons": reasons,
        }

    failed_equipment: set[str] = set()
    first_effects = branches[0][1] if branches else _Effects()

    for candidate, effects in branches:
        finished, equipment_reasons = _finish_recipe(candidate, effects, available)
        if not equipment_reasons:
            return {
                "possible": True,
                "recipe": finished,
                "choices": _ordered_unique(effects.choices),
                "warnings": sorted(set(effects.warnings)),
                "reasons": [],
            }
        failed_equipment.update(equipment_reasons)

    return {
        "possible": False,
        "recipe": None,
        "choices": _ordered_unique(first_effects.choices),
        "warnings": sorted(set(first_effects.warnings)),
        "reasons": sorted(failed_equipment),
    }


def print_original(recipe: dict[str, Any]) -> str:
    return recipe["authored_text"]
