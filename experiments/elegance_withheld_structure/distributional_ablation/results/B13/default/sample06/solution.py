from copy import deepcopy
from fractions import Fraction
from typing import Any, Dict, Iterable, List, MutableMapping, MutableSequence, Sequence, Tuple


def print_original(recipe: dict) -> str:
    """Return the recipe's original authored text without changing the recipe."""
    return recipe["authored_text"]


def _catalog_entries(catalog: Any) -> List[dict]:
    if isinstance(catalog, (list, tuple)):
        return list(catalog)
    if isinstance(catalog, dict):
        for key in ("choices", "substitutions", "catalog"):
            value = catalog.get(key)
            if isinstance(value, (list, tuple)):
                return list(value)
    return []


def _ingredient_lists(value: Any) -> Iterable[MutableSequence[dict]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "ingredients" and isinstance(child, list):
                yield child
            elif key != "authored_text":
                yield from _ingredient_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _ingredient_lists(child)


def _equipment_lists(value: Any) -> Iterable[MutableSequence[str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "equipment" and isinstance(child, list):
                yield child
            elif key != "authored_text":
                yield from _equipment_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _equipment_lists(child)


def _scale_recipe(value: Any, factor: Fraction, root: bool = False) -> None:
    if isinstance(value, dict):
        if not root and "yield" in value and isinstance(value["yield"], Fraction):
            value["yield"] *= factor

        for key, child in value.items():
            if key == "authored_text":
                continue
            if key == "ingredients" and isinstance(child, list):
                for ingredient in child:
                    if isinstance(ingredient, dict) and "quantity" in ingredient:
                        ingredient["quantity"] *= factor
                    _scale_recipe(ingredient, factor)
            else:
                _scale_recipe(child, factor)
    elif isinstance(value, list):
        for child in value:
            _scale_recipe(child, factor)


def _result_values(result: dict, *keys: str) -> List[Any]:
    values: List[Any] = []
    for key in keys:
        value = result.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            values.extend(value)
        else:
            values.append(value)
    return values


def _wording_pairs(result: dict) -> List[Tuple[str, str]]:
    changes = result.get("wording_changes", [])
    if isinstance(changes, dict):
        return [(str(old), str(new)) for old, new in changes.items()]

    pairs: List[Tuple[str, str]] = []
    if isinstance(changes, (list, tuple)):
        for change in changes:
            if isinstance(change, dict) and "old" in change and "new" in change:
                pairs.append((str(change["old"]), str(change["new"])))
            elif isinstance(change, (list, tuple)) and len(change) == 2:
                pairs.append((str(change[0]), str(change[1])))
    return pairs


def _apply_instruction_changes(value: Any, changes: Sequence[Tuple[str, str]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "instructions" and isinstance(child, list):
                for index, instruction in enumerate(child):
                    if not isinstance(instruction, str):
                        continue
                    edited = instruction
                    for old, new in changes:
                        edited = edited.replace(old, new)
                    child[index] = edited
            elif key != "authored_text":
                _apply_instruction_changes(child, changes)
    elif isinstance(value, list):
        for child in value:
            _apply_instruction_changes(child, changes)


def adapt(recipe: dict, request: dict, catalog: Any) -> dict:
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    target_yield = request["target_yield"]
    original_yield = adapted["yield"]
    scale = target_yield / original_yield

    _scale_recipe(adapted, scale, root=True)
    adapted["yield"] = target_yield

    excluded = set(request.get("excluded", []))
    available = set(request.get("available_equipment", []))
    choices: List[str] = []
    warnings: set[str] = set()
    reasons: set[str] = set()
    wording_changes: List[Tuple[str, str]] = []

    indexed_catalog = list(enumerate(_catalog_entries(catalog)))
    indexed_catalog.sort(key=lambda item: (item[1].get("priority", 0), item[0]))

    by_ingredient: Dict[str, List[dict]] = {}
    for _, choice in indexed_catalog:
        target = choice.get("for")
        if isinstance(target, str):
            by_ingredient.setdefault(target, []).append(choice)

    def change_equipment(result: dict) -> None:
        removals = _result_values(
            result,
            "equipment_removals",
            "equipment_remove",
            "remove_equipment",
        )
        additions = _result_values(
            result,
            "equipment_additions",
            "equipment_add",
            "add_equipment",
        )

        equipment_lists = list(_equipment_lists(adapted))
        for equipment in equipment_lists:
            if removals:
                equipment[:] = [item for item in equipment if item not in removals]

        if additions:
            root_equipment = adapted.setdefault("equipment", [])
            for item in additions:
                if item not in root_equipment:
                    root_equipment.append(item)

    def resolve(
        ingredient: MutableMapping[str, Any],
        owner: MutableSequence[dict],
        ancestry: Tuple[str, ...],
    ) -> None:
        name = ingredient.get("name")
        if name not in excluded:
            return

        if name in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            return

        candidates = by_ingredient.get(name, [])
        if not candidates:
            reasons.add(f"no substitution for {name}")
            return

        choice = candidates[0]
        choices.append(choice["id"])
        result = choice.get("result", {})
        next_ancestry = ancestry + (name,)

        ingredient["name"] = result["name"]
        if "quantity_factor" in result:
            ingredient["quantity"] *= result["quantity_factor"]
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        wording_changes.extend(_wording_pairs(result))
        change_equipment(result)

        resolve(ingredient, owner, next_ancestry)

        additions = _result_values(
            result,
            "additional_ingredients",
            "additional",
        )
        for extra_source in additions:
            if not isinstance(extra_source, dict):
                continue
            extra = deepcopy(extra_source)
            if "quantity" in extra:
                extra["quantity"] *= scale
            owner.append(extra)
            resolve(extra, owner, next_ancestry)

    initial_lists = list(_ingredient_lists(adapted))
    for ingredients in initial_lists:
        for ingredient in list(ingredients):
            if isinstance(ingredient, dict):
                resolve(ingredient, ingredients, ())

    _apply_instruction_changes(adapted, wording_changes)

    for equipment in _equipment_lists(adapted):
        equipment[:] = sorted(set(equipment))
        for item in equipment:
            if item not in available:
                reasons.add(f"equipment {item} unavailable")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
