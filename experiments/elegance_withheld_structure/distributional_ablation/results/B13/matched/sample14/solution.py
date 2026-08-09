"""Recipe adaptation with exact arithmetic and deterministic substitutions."""

from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterable


def print_original(recipe: dict[str, Any]) -> str:
    """Return the recipe's authored text byte-for-byte."""
    return recipe["authored_text"]


def _key(value: Any) -> str:
    return str(value).casefold()


def _sections(value: Any) -> Iterable[dict[str, Any]]:
    """Yield a recipe/component and its recursively nested components."""
    if not isinstance(value, dict):
        return

    yield value
    components = value.get("components", [])

    if isinstance(components, dict):
        children = components.values()
    elif isinstance(components, (list, tuple)):
        children = components
    else:
        children = ()

    for component in children:
        yield from _sections(component)


def _ingredient_lists(recipe: dict[str, Any]) -> Iterable[list[dict[str, Any]]]:
    for section in _sections(recipe):
        ingredients = section.get("ingredients")
        if isinstance(ingredients, list):
            yield ingredients


def _replace_instruction_text(
    recipe: dict[str, Any], old: str, new: str
) -> None:
    for section in _sections(recipe):
        instructions = section.get("instructions")
        if not isinstance(instructions, list):
            continue
        for index, instruction in enumerate(instructions):
            if isinstance(instruction, str):
                instructions[index] = instruction.replace(old, new)


def _equipment_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _change_equipment(
    recipe: dict[str, Any], additions: Any, removals: Any
) -> None:
    removal_keys = {_key(item) for item in _equipment_values(removals)}

    for section in _sections(recipe):
        equipment = section.get("equipment")
        if isinstance(equipment, (list, tuple, set)):
            section["equipment"] = [
                str(item)
                for item in equipment
                if _key(item) not in removal_keys
            ]

    root_equipment = _equipment_values(recipe.get("equipment", []))
    root_equipment.extend(_equipment_values(additions))
    recipe["equipment"] = root_equipment


def _result_equipment(result: dict[str, Any], kind: str) -> Any:
    if kind == "add":
        keys = ("equipment_additions", "equipment_add", "add_equipment")
    else:
        keys = ("equipment_removals", "equipment_remove", "remove_equipment")

    for key in keys:
        if key in result:
            return result[key]
    return []


def _catalog_matches(
    catalog: list[dict[str, Any]], ingredient: dict[str, Any]
) -> list[dict[str, Any]]:
    names = {
        _key(ingredient.get("name", "")),
        _key(ingredient.get("id", "")),
    }
    matches = []

    for order, choice in enumerate(catalog):
        subject = choice.get("for")
        subjects = subject if isinstance(subject, (list, tuple, set)) else [subject]
        if any(_key(item) in names for item in subjects):
            matches.append((choice.get("priority", 0), order, choice))

    matches.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in matches]


def _warning_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def adapt(
    recipe: dict[str, Any],
    request: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return an adapted recipe or every independently applicable failure."""
    adapted = deepcopy(recipe)
    excluded = {_key(item) for item in request.get("excluded", [])}
    available = {
        _key(item) for item in request.get("available_equipment", [])
    }

    choices: list[str] = []
    warnings: list[str] = []
    reasons: set[str] = set()
    current_yield = Fraction(adapted["yield"])

    # Each item carries its substitution ancestry. This detects A -> B -> A
    # without treating unrelated appearances of A as a cycle.
    work: list[
        tuple[list[dict[str, Any]], dict[str, Any], tuple[str, ...]]
    ] = []
    for ingredients in _ingredient_lists(adapted):
        work.extend((ingredients, ingredient, ()) for ingredient in ingredients)

    position = 0
    while position < len(work):
        owner, ingredient, ancestry = work[position]
        position += 1

        name = str(ingredient.get("name", ""))
        normalized = _key(name)
        if normalized not in excluded:
            continue

        if normalized in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            continue

        matches = _catalog_matches(catalog, ingredient)
        if not matches:
            reasons.add(f"no substitution for {name}")
            continue

        choice = matches[0]
        result = choice.get("result", {})
        choices.append(str(choice["id"]))
        warnings.extend(_warning_values(choice.get("warnings")))
        warnings.extend(
            _warning_values(result.get("warnings", result.get("warning")))
        )

        if "name" in result:
            ingredient["name"] = result["name"]
        if "quantity_factor" in result:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"])
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        wording_changes = result.get("wording_changes", [])
        if isinstance(wording_changes, dict):
            wording_changes = [wording_changes]

        for change in wording_changes:
            old = str(change["old"])
            new = str(change["new"])
            _replace_instruction_text(adapted, old, new)

            preparation = ingredient.get("preparation")
            if isinstance(preparation, str):
                ingredient["preparation"] = preparation.replace(old, new)

        _change_equipment(
            adapted,
            _result_equipment(result, "add"),
            _result_equipment(result, "remove"),
        )

        if "yield" in result:
            current_yield = Fraction(result["yield"])
        if "yield_factor" in result:
            current_yield *= Fraction(result["yield_factor"])

        additions = result.get("additional_ingredients", [])
        if isinstance(additions, dict):
            additions = [additions]

        path = ancestry + (normalized,)
        added_tasks = []
        for additional in additions:
            added = deepcopy(additional)
            owner.append(added)
            added_tasks.append((owner, added, path))

        # Reconsider the replacement immediately, followed by any introduced
        # ingredients, before proceeding to the next original ingredient.
        work[position:position] = [
            (owner, ingredient, path),
            *added_tasks,
        ]

    target_yield = Fraction(request["target_yield"])
    scale = target_yield / current_yield
    for ingredients in _ingredient_lists(adapted):
        for ingredient in ingredients:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"]) * scale
            )
    adapted["yield"] = target_yield

    all_equipment: set[str] = set()
    for section in _sections(adapted):
        equipment = section.get("equipment")
        if isinstance(equipment, (list, tuple, set)):
            normalized_equipment = sorted({str(item) for item in equipment})
            section["equipment"] = normalized_equipment
            all_equipment.update(normalized_equipment)

    for item in all_equipment:
        if _key(item) not in available:
            reasons.add(f"equipment {item} unavailable")

    return {
        "possible": not reasons,
        "recipe": None if reasons else adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }
