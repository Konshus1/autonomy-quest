from copy import deepcopy
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Tuple


def print_original(recipe: dict) -> str:
    """Return the recipe's original authored representation unchanged."""
    return recipe["authored_text"]


def _normalized(value: Any) -> str:
    return str(value).casefold()


def _ingredient_lists(value: Any) -> Iterable[List[dict]]:
    if isinstance(value, dict):
        ingredients = value.get("ingredients")
        if isinstance(ingredients, list):
            yield ingredients
        for key, child in value.items():
            if key != "ingredients":
                yield from _ingredient_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _ingredient_lists(child)


def _instruction_lists(value: Any) -> Iterable[List[str]]:
    if isinstance(value, dict):
        instructions = value.get("instructions")
        if isinstance(instructions, list):
            yield instructions
        for key, child in value.items():
            if key != "instructions":
                yield from _instruction_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _instruction_lists(child)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _collect_warnings(choice: dict, result: dict, warnings: set) -> None:
    for source in (choice, result):
        for key in ("warning", "warnings"):
            for warning in _as_list(source.get(key)):
                warnings.add(str(warning))


def _equipment_changes(result: dict) -> Tuple[List[str], List[str]]:
    additions = _as_list(
        result.get("equipment_additions", result.get("add_equipment"))
    )
    removals = _as_list(
        result.get("equipment_removals", result.get("remove_equipment"))
    )

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions.extend(
            _as_list(equipment.get("additions", equipment.get("add")))
        )
        removals.extend(
            _as_list(equipment.get("removals", equipment.get("remove")))
        )

    return [str(item) for item in additions], [str(item) for item in removals]


def _wording_changes(result: dict) -> Iterable[Tuple[str, str]]:
    for change in _as_list(result.get("wording_changes")):
        if isinstance(change, dict) and "old" in change and "new" in change:
            yield str(change["old"]), str(change["new"])
        elif isinstance(change, (list, tuple)) and len(change) == 2:
            yield str(change[0]), str(change[1])


def _additional_ingredients(result: dict) -> List[dict]:
    additions = result.get("additional_ingredients", [])
    if additions is None:
        return []
    if isinstance(additions, dict):
        additions = [additions]
    return [deepcopy(item) for item in additions]


def adapt(recipe: dict, request: dict, catalog: list) -> dict:
    """Adapt a recipe without modifying the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    current_yield = Fraction(adapted["yield"])
    if current_yield == 0:
        raise ValueError("recipe yield must not be zero")

    excluded = {_normalized(name) for name in request.get("excluded", [])}
    available_equipment = set(request.get("available_equipment", []))

    indexed_catalog: Dict[str, List[Tuple[Any, int, dict]]] = {}
    for position, choice in enumerate(catalog):
        indexed_catalog.setdefault(_normalized(choice["for"]), []).append(
            (choice["priority"], position, choice)
        )
    for choices_for_ingredient in indexed_catalog.values():
        choices_for_ingredient.sort(key=lambda item: (item[0], item[1]))

    equipment = set(adapted.get("equipment", []))
    chosen_ids: List[Any] = []
    warnings: set = set()
    reasons: set = set()
    wording: List[Tuple[str, str]] = []

    queue: List[Tuple[dict, List[dict], Tuple[str, ...]]] = []
    for ingredients in _ingredient_lists(adapted):
        for ingredient in ingredients:
            queue.append((ingredient, ingredients, ()))

    index = 0
    while index < len(queue):
        ingredient, container, ancestry = queue[index]
        index += 1

        name = str(ingredient["name"])
        normalized_name = _normalized(name)
        if normalized_name not in excluded:
            continue

        if normalized_name in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            continue

        candidates = indexed_catalog.get(normalized_name, [])
        if not candidates:
            reasons.add(f"no substitution for {name}")
            continue

        choice = candidates[0][2]
        result = choice.get("result", {})
        chosen_ids.append(choice["id"])
        _collect_warnings(choice, result, warnings)

        ingredient["name"] = result.get("name", ingredient["name"])
        ingredient["quantity"] = Fraction(ingredient["quantity"]) * Fraction(
            result.get("quantity_factor", 1)
        )
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        additions, removals = _equipment_changes(result)
        equipment.difference_update(removals)
        equipment.update(additions)
        wording.extend(_wording_changes(result))

        if "yield_factor" in result:
            current_yield *= Fraction(result["yield_factor"])
        if "yield" in result:
            current_yield = Fraction(result["yield"])

        next_ancestry = ancestry + (normalized_name,)
        replacement_name = _normalized(ingredient["name"])
        if replacement_name in excluded:
            queue.append((ingredient, container, next_ancestry))

        for additional in _additional_ingredients(result):
            container.append(additional)
            queue.append((additional, container, next_ancestry))

    if current_yield == 0:
        raise ValueError("substitution yield must not be zero")

    scale = target_yield / current_yield
    for ingredients in _ingredient_lists(adapted):
        for ingredient in ingredients:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    for instructions in _instruction_lists(adapted):
        for instruction_index, instruction in enumerate(instructions):
            final_text = instruction
            for old, new in wording:
                final_text = final_text.replace(old, new)
            instructions[instruction_index] = final_text

    adapted["yield"] = target_yield
    adapted["equipment"] = sorted(equipment)

    for item in sorted(equipment - available_equipment):
        reasons.add(f"equipment {item} unavailable")

    result = {
        "possible": not reasons,
        "recipe": adapted if not reasons else None,
        "choices": chosen_ids,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }
    return result
