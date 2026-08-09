"""Recipe adaptation using only the Python standard library."""

from copy import deepcopy
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


def _key(value: Any) -> str:
    return str(value).strip().casefold()


def _component_values(components: Any) -> Iterable[Any]:
    if isinstance(components, list):
        yield from components
    elif isinstance(components, dict):
        if "ingredients" in components or "instructions" in components:
            yield components
        else:
            yield from components.values()


def _ingredient_lists(node: Any) -> Iterable[List[MutableMapping[str, Any]]]:
    if not isinstance(node, dict):
        return

    ingredients = node.get("ingredients")
    if isinstance(ingredients, list):
        yield ingredients

    for component in _component_values(node.get("components", [])):
        yield from _ingredient_lists(component)


def _instruction_lists(node: Any) -> Iterable[List[str]]:
    if not isinstance(node, dict):
        return

    instructions = node.get("instructions")
    if isinstance(instructions, list):
        yield instructions

    for component in _component_values(node.get("components", [])):
        yield from _instruction_lists(component)


def _messages(value: Any) -> Iterable[str]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return (str(item) for item in value)
    return (str(value),)


def _equipment_delta(result: Mapping[str, Any]) -> Tuple[Iterable[Any], Iterable[Any]]:
    additions = result.get(
        "equipment_additions",
        result.get("equipment_add", result.get("add_equipment", ())),
    )
    removals = result.get(
        "equipment_removals",
        result.get("equipment_remove", result.get("remove_equipment", ())),
    )

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions = equipment.get("additions", equipment.get("add", additions))
        removals = equipment.get("removals", equipment.get("remove", removals))

    return additions or (), removals or ()


def _result_yield_factor(
    result: Mapping[str, Any], original_yield: Fraction
) -> Fraction:
    if "yield_factor" in result:
        return Fraction(result["yield_factor"])
    if "yield" in result:
        return Fraction(result["yield"]) / original_yield
    return Fraction(1)


def adapt(
    recipe: Mapping[str, Any],
    request: Mapping[str, Any],
    catalog: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Return a complete adaptation result without mutating any input."""
    adapted = deepcopy(recipe)
    original_yield = Fraction(recipe["yield"])
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / original_yield

    ingredient_lists = list(_ingredient_lists(adapted))
    for ingredients in ingredient_lists:
        for ingredient in ingredients:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    ranked: Dict[str, List[Tuple[Fraction, int, Mapping[str, Any]]]] = {}
    for position, choice in enumerate(catalog):
        catalog_key = _key(choice.get("for", ""))
        ranked.setdefault(catalog_key, []).append(
            (Fraction(choice.get("priority", 0)), position, choice)
        )

    for candidates in ranked.values():
        candidates.sort(key=lambda item: (item[0], item[1]))

    excluded = {_key(name) for name in request.get("excluded", [])}
    choices: List[Any] = []
    warnings = set()
    reasons = set()
    wording_changes: List[Tuple[str, str]] = []
    required_equipment = set(adapted.get("equipment", []))
    cumulative_yield_factor = Fraction(1)

    def choose(ingredient: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        candidates = ranked.get(_key(ingredient.get("name", "")), [])
        if not candidates:
            candidates = ranked.get(_key(ingredient.get("id", "")), [])
        return candidates[0][2] if candidates else None

    def resolve(
        ingredient: MutableMapping[str, Any],
        ancestry: Tuple[str, ...],
        destination: List[MutableMapping[str, Any]],
    ) -> None:
        nonlocal cumulative_yield_factor

        name = str(ingredient.get("name", ""))
        name_key = _key(name)
        if name_key not in excluded:
            destination.append(ingredient)
            return

        choice = choose(ingredient)
        if choice is None:
            reasons.add("no substitution for " + name)
            return

        choices.append(choice["id"])
        result_value = choice.get("result", {})
        result = result_value if isinstance(result_value, dict) else {}

        warnings.update(_messages(choice.get("warnings", choice.get("warning"))))
        warnings.update(_messages(result.get("warnings", result.get("warning"))))

        additions, removals = _equipment_delta(result)
        required_equipment.update(additions)
        required_equipment.difference_update(removals)

        for change in result.get("wording_changes", []) or []:
            if isinstance(change, dict) and "old" in change and "new" in change:
                wording_changes.append((str(change["old"]), str(change["new"])))

        cumulative_yield_factor *= _result_yield_factor(result, original_yield)

        replacement = deepcopy(ingredient)
        replacement["name"] = result.get("name", replacement.get("name"))
        replacement["quantity"] = (
            Fraction(replacement["quantity"])
            * Fraction(result.get("quantity_factor", 1))
        )
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        replacement_key = _key(replacement.get("name", ""))
        current_path = ancestry + (name_key,)
        if replacement_key in excluded and replacement_key in current_path:
            reasons.add(
                "substitution cycle involving " + str(replacement.get("name", ""))
            )
        else:
            resolve(replacement, current_path, destination)

        for additional in result.get("additional_ingredients", []) or []:
            extra = deepcopy(additional)
            extra["quantity"] = Fraction(extra.get("quantity", 0)) * scale
            resolve(extra, current_path, destination)

    for ingredients in ingredient_lists:
        resolved: List[MutableMapping[str, Any]] = []
        for ingredient in list(ingredients):
            resolve(ingredient, (), resolved)
        ingredients[:] = resolved

    if cumulative_yield_factor != 1:
        correction = Fraction(1) / cumulative_yield_factor
        for ingredients in ingredient_lists:
            for ingredient in ingredients:
                ingredient["quantity"] = (
                    Fraction(ingredient["quantity"]) * correction
                )

    for instructions in _instruction_lists(adapted):
        for index, instruction in enumerate(instructions):
            final_text = instruction
            for old, new in wording_changes:
                final_text = final_text.replace(old, new)
            instructions[index] = final_text

    adapted["yield"] = target_yield
    adapted["equipment"] = sorted(required_equipment)

    available = {_key(item) for item in request.get("available_equipment", [])}
    for equipment in required_equipment:
        if _key(equipment) not in available:
            reasons.add("equipment " + str(equipment) + " unavailable")

    possible = not reasons
    return {
        "possible": possible,
        "recipe": adapted if possible else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }


def print_original(recipe: Mapping[str, Any]) -> str:
    """Return the authored recipe text exactly as supplied."""
    return recipe["authored_text"]
