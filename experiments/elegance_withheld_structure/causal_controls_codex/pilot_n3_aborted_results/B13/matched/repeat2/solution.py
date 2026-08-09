"""Recipe adaptation using only Python's standard library."""

from copy import deepcopy
from fractions import Fraction
import re


def print_original(recipe):
    """Return the recipe's authored text exactly as supplied."""
    return recipe["authored_text"]


def _same(left, right):
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and left.casefold() == right.casefold()
    )


def _sections(recipe):
    """Yield the recipe and recipe-like nested components."""
    yield recipe
    components = recipe.get("components", [])
    values = components.values() if isinstance(components, dict) else components
    for component in values:
        if isinstance(component, dict):
            yield from _sections(component)


def _replace_text(text, old, new):
    if not isinstance(text, str) or not old or old == new:
        return text
    pattern = r"(?<!\w)" + re.escape(old) + r"(?!\w)"
    return re.sub(pattern, lambda match: new, text, flags=re.IGNORECASE)


def _rewrite_all(recipe, old, new):
    for section in _sections(recipe):
        instructions = section.get("instructions", [])
        for index, instruction in enumerate(instructions):
            instructions[index] = _replace_text(instruction, old, new)

        for ingredient in section.get("ingredients", []):
            preparation = ingredient.get("preparation")
            if isinstance(preparation, str):
                ingredient["preparation"] = _replace_text(
                    preparation, old, new
                )


def _change_equipment(recipe, result):
    removals = result.get(
        "equipment_removals", result.get("remove_equipment", [])
    )
    additions = result.get(
        "equipment_additions", result.get("add_equipment", [])
    )

    equipment = recipe["equipment"]
    equipment[:] = [
        item
        for item in equipment
        if not any(_same(item, removal) for removal in removals)
    ]

    for item in additions:
        if not any(_same(item, present) for present in equipment):
            equipment.append(item)


def _is_excluded(name, exclusions):
    return any(_same(name, excluded) for excluded in exclusions)


def _matching_choice(ingredient, catalog):
    name = ingredient.get("name")
    ingredient_id = ingredient.get("id")
    candidates = []

    for index, choice in enumerate(catalog):
        target = choice.get("for")
        if _same(target, name) or _same(target, ingredient_id):
            candidates.append((choice["priority"], index, choice))

    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _append_warnings(destination, value):
    if value is None:
        return
    if isinstance(value, str):
        destination.append(value)
    else:
        destination.extend(value)


def adapt(recipe, request, catalog):
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    exclusions = tuple(request.get("excluded", []))
    choices = []
    warnings = []
    reasons = set()

    # Queue entries are (owning section, ingredient, replacement ancestry).
    queue = []
    for section in list(_sections(adapted)):
        for ingredient in list(section.get("ingredients", [])):
            queue.append((section, ingredient, ()))

    cursor = 0
    while cursor < len(queue):
        section, ingredient, ancestry = queue[cursor]
        cursor += 1

        name = ingredient.get("name", "")
        if not _is_excluded(name, exclusions):
            continue

        folded_name = str(name).casefold()
        if folded_name in ancestry:
            reasons.add("substitution cycle involving " + str(name))
            continue

        choice = _matching_choice(ingredient, catalog)
        if choice is None:
            reasons.add("no substitution for " + str(name))
            continue

        choices.append(choice["id"])
        result = choice.get("result", {})
        old_name = name
        new_name = result.get("name", old_name)

        ingredient["name"] = new_name
        ingredient["quantity"] *= result.get(
            "quantity_factor", Fraction(1, 1)
        )

        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        _rewrite_all(adapted, old_name, new_name)
        for change in result.get("wording_changes", []):
            _rewrite_all(adapted, change["old"], change["new"])

        _change_equipment(adapted, result)

        # Support an absolute effective yield and a multiplicative yield change.
        if "yield" in result:
            adapted["yield"] = result["yield"]
        if "yield_factor" in result:
            adapted["yield"] *= result["yield_factor"]

        _append_warnings(warnings, choice.get("warnings"))
        _append_warnings(warnings, result.get("warnings"))

        next_ancestry = ancestry + (folded_name,)
        queue.append((section, ingredient, next_ancestry))

        ingredients = section.setdefault("ingredients", [])
        for additional in result.get("additional_ingredients", []):
            added = deepcopy(additional)
            ingredients.append(added)
            queue.append((section, added, ()))

    available = tuple(request.get("available_equipment", []))
    for section in _sections(adapted):
        if "equipment" not in section:
            continue

        unique = []
        for item in section["equipment"]:
            if not any(_same(item, present) for present in unique):
                unique.append(item)
        section["equipment"] = sorted(unique)

        for item in section["equipment"]:
            if not any(_same(item, present) for present in available):
                reasons.add("equipment " + str(item) + " unavailable")

    # Scale only after substitutions have established the effective yield.
    target_yield = request["target_yield"]
    scale = target_yield / adapted["yield"]
    for section in _sections(adapted):
        for ingredient in section.get("ingredients", []):
            ingredient["quantity"] *= scale
    adapted["yield"] = target_yield

    result_reasons = sorted(reasons)
    result_warnings = sorted(warnings)

    if result_reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": result_warnings,
            "reasons": result_reasons,
        }

    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": result_warnings,
        "reasons": [],
    }
