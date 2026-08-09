"""Exact, deterministic recipe adaptation using only the standard library."""

from copy import deepcopy
from fractions import Fraction
import re


def print_original(recipe):
    """Return the recipe exactly as authored."""
    return recipe["authored_text"]


def _key(value):
    return str(value).casefold()


def _records(value):
    """Yield the recipe and recursively nested component records."""
    if not isinstance(value, dict):
        return
    yield value
    components = value.get("components", [])
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                yield from _records(component)


def _ingredient_lists(recipe):
    for record in _records(recipe):
        ingredients = record.get("ingredients")
        if isinstance(ingredients, list):
            yield ingredients


def _replace_phrase(text, old, new):
    if not old or old == new:
        return text
    pattern = r"(?<!\w)" + re.escape(old) + r"(?!\w)"
    return re.sub(pattern, lambda match: new, text, flags=re.IGNORECASE)


def _rewrite_instructions(recipe, old, new):
    for record in _records(recipe):
        instructions = record.get("instructions")
        if isinstance(instructions, list):
            record["instructions"] = [
                _replace_phrase(item, old, new)
                if isinstance(item, str)
                else item
                for item in instructions
            ]


def _equipment_delta(result):
    additions = result.get(
        "equipment_additions",
        result.get("equipment_add", result.get("add_equipment", [])),
    )
    removals = result.get(
        "equipment_removals",
        result.get("equipment_remove", result.get("remove_equipment", [])),
    )
    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions = equipment.get("add", additions)
        removals = equipment.get("remove", removals)
    return list(additions or []), list(removals or [])


def _apply_equipment(recipe, additions, removals):
    removed = {_key(item) for item in removals}

    for record in _records(recipe):
        equipment = record.get("equipment")
        if isinstance(equipment, list):
            equipment[:] = [
                item for item in equipment if _key(item) not in removed
            ]

    root_equipment = recipe.setdefault("equipment", [])
    present = {_key(item) for item in root_equipment}
    for item in additions:
        if _key(item) not in present:
            root_equipment.append(item)
            present.add(_key(item))


def _choice_warnings(choice, result):
    for source in (choice, result):
        warning = source.get("warning")
        if isinstance(warning, str):
            yield warning

        warnings = source.get("warnings", [])
        if isinstance(warnings, str):
            yield warnings
        elif isinstance(warnings, list):
            for item in warnings:
                if isinstance(item, str):
                    yield item


def adapt(recipe, request, catalog):
    """Adapt a recipe without modifying recipe, request, or catalog."""
    work = deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    current_yield = Fraction(work["yield"])
    if current_yield == 0:
        raise ValueError("recipe yield must not be zero")

    excluded = {_key(item) for item in request.get("excluded", [])}
    available = {
        _key(item) for item in request.get("available_equipment", [])
    }

    ordered_catalog = sorted(
        enumerate(catalog),
        key=lambda pair: (pair[1].get("priority", 0), pair[0]),
    )

    def candidates(ingredient):
        identifiers = {
            _key(ingredient.get("name", "")),
            _key(ingredient.get("id", "")),
        }
        return [
            choice
            for _, choice in ordered_catalog
            if _key(choice.get("for", "")) in identifiers
        ]

    # Entries are (ingredient, containing ingredient list, ancestry).
    queue = []
    for ingredients in _ingredient_lists(work):
        queue.extend(
            (ingredient, ingredients, ()) for ingredient in list(ingredients)
        )

    selected = []
    warnings = set()
    reasons = set()
    cursor = 0

    while cursor < len(queue):
        ingredient, containing_list, ancestry = queue[cursor]
        cursor += 1

        name = str(ingredient.get("name", ""))
        name_key = _key(name)
        if name_key not in excluded:
            continue

        if name_key in ancestry:
            reasons.add("substitution cycle involving " + name)
            continue

        options = candidates(ingredient)
        if not options:
            reasons.add("no substitution for " + name)
            continue

        choice = options[0]
        result = choice.get("result", {})
        selected.append(choice["id"])
        warnings.update(_choice_warnings(choice, result))

        replacement_name = str(result.get("name", name))
        ingredient["name"] = replacement_name

        if "quantity_factor" in result:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"])
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        # Plain authored instructions have no reference keys, so apply direct,
        # phrase-bounded edits to every instruction collection.
        _rewrite_instructions(work, name, replacement_name)
        for change in result.get("wording_changes", []) or []:
            if isinstance(change, dict):
                _rewrite_instructions(
                    work,
                    str(change.get("old", "")),
                    str(change.get("new", "")),
                )

        additions, removals = _equipment_delta(result)
        _apply_equipment(work, additions, removals)

        # Both relative and absolute yield descriptions are supported. The final
        # scaling pass always restores the request's target yield.
        if "yield_factor" in result:
            current_yield *= Fraction(result["yield_factor"])
        if "yield" in result:
            current_yield = Fraction(result["yield"])
        if current_yield == 0:
            raise ValueError("substitution yield must not be zero")

        next_ancestry = ancestry + (name_key,)
        queue.append((ingredient, containing_list, next_ancestry))

        for additional in deepcopy(
            result.get("additional_ingredients", []) or []
        ):
            containing_list.append(additional)
            queue.append((additional, containing_list, next_ancestry))

    scale = target_yield / current_yield
    for ingredients in _ingredient_lists(work):
        for ingredient in ingredients:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"]) * scale
            )
    work["yield"] = target_yield

    all_equipment = []
    for record in _records(work):
        equipment = record.get("equipment")
        if not isinstance(equipment, list):
            continue

        unique = {}
        for item in equipment:
            unique.setdefault(_key(item), item)
        record["equipment"] = sorted(unique.values())
        all_equipment.extend(record["equipment"])

    for item in all_equipment:
        if _key(item) not in available:
            reasons.add("equipment " + str(item) + " unavailable")

    possible = not reasons
    return {
        "possible": possible,
        "recipe": work if possible else None,
        "choices": selected,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }
