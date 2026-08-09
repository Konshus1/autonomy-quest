"""Recipe adaptation with exact rational arithmetic."""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the original authored text byte-for-byte as a string."""
    return recipe["authored_text"]


def _key(value):
    return str(value).casefold()


def _containers(value):
    if not isinstance(value, dict):
        return
    if isinstance(value.get("ingredients"), list):
        yield value
    components = value.get("components", [])
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                yield from _containers(component)


def _instruction_lists(value):
    if not isinstance(value, dict):
        return
    if isinstance(value.get("instructions"), list):
        yield value["instructions"]
    components = value.get("components", [])
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                yield from _instruction_lists(component)


def _ingredient_slots(recipe):
    for container in _containers(recipe):
        ingredients = container["ingredients"]
        for index, ingredient in enumerate(ingredients):
            if isinstance(ingredient, dict):
                yield ingredients, index, ingredient


def _authored_equipment(recipe):
    equipment = set(recipe.get("equipment", []) or [])
    for container in _containers(recipe):
        equipment.update(container.get("equipment", []) or [])
    return equipment


def _effective_equipment(recipe):
    if "_adapt_equipment" in recipe:
        return set(recipe["_adapt_equipment"])
    return _authored_equipment(recipe)


def _ordered_catalog(catalog):
    raw = catalog.get("choices", []) if isinstance(catalog, dict) else catalog
    ordered = []
    for position, choice in enumerate(raw or []):
        if isinstance(choice, dict):
            copied = deepcopy(choice)
            copied["_adapt_position"] = position
            ordered.append(copied)
    ordered.sort(
        key=lambda choice: (choice.get("priority", 0), choice["_adapt_position"])
    )
    return ordered


def _matching_choices(ingredient, catalog):
    targets = {
        _key(ingredient.get("name", "")),
        _key(ingredient.get("id", "")),
    }
    return [choice for choice in catalog if _key(choice.get("for", "")) in targets]


def _change_wording(recipe, changes):
    for instructions in _instruction_lists(recipe):
        for index, statement in enumerate(instructions):
            text = statement
            for change in changes:
                if isinstance(change, dict):
                    old = change.get("old", "")
                    new = change.get("new", "")
                else:
                    try:
                        old, new = change
                    except (TypeError, ValueError):
                        continue
                if old:
                    text = text.replace(str(old), str(new))
            instructions[index] = text


def _equipment_changes(result):
    nested = result.get("equipment", {})
    if not isinstance(nested, dict):
        nested = {}

    additions = result.get(
        "equipment_add",
        result.get(
            "equipment_additions",
            result.get("add_equipment", nested.get("add", [])),
        ),
    )
    removals = result.get(
        "equipment_remove",
        result.get(
            "equipment_removals",
            result.get("remove_equipment", nested.get("remove", [])),
        ),
    )
    return additions or [], removals or []


def _apply_choice(recipe, ingredient_list, index, choice, ancestry):
    result = choice.get("result", {}) or {}
    ingredient = ingredient_list[index]
    old_name = ingredient.get("name", "")

    replacement = deepcopy(ingredient)
    replacement["name"] = result.get("name", replacement.get("name"))
    if "quantity_factor" in result:
        replacement["quantity"] = (
            replacement.get("quantity", Fraction(0))
            * result["quantity_factor"]
        )
    if "unit" in result:
        replacement["unit"] = result["unit"]
    if "preparation" in result:
        replacement["preparation"] = result["preparation"]
    replacement["_adapt_ancestry"] = ancestry + (_key(old_name),)
    ingredient_list[index] = replacement

    for additional in result.get("additional_ingredients", []) or []:
        added = deepcopy(additional)
        added["_adapt_ancestry"] = ancestry + (_key(old_name),)
        ingredient_list.append(added)

    additions, removals = _equipment_changes(result)
    equipment = _effective_equipment(recipe)
    removal_keys = {_key(item) for item in removals}
    equipment = {item for item in equipment if _key(item) not in removal_keys}
    equipment.update(additions)
    recipe["_adapt_equipment"] = list(equipment)

    _change_wording(recipe, result.get("wording_changes", []) or [])

    if "yield" in result:
        recipe["_adapt_yield"] = result["yield"]
    if "yield_factor" in result:
        recipe["_adapt_yield"] *= result["yield_factor"]

    warnings = result.get("warnings", []) or []
    if isinstance(warnings, str):
        warnings = [warnings]
    if "warning" in result:
        warnings = list(warnings) + [result["warning"]]
    recipe["_adapt_warnings"].extend(warnings)


def _unavailable_reasons(recipe, available):
    available_keys = {_key(item) for item in available}
    return {
        "equipment %s unavailable" % item
        for item in _effective_equipment(recipe)
        if _key(item) not in available_keys
    }


def _excluded_state(recipe, excluded, catalog):
    blockers = set()
    adaptable = []

    for ordinal, (_, _, ingredient) in enumerate(_ingredient_slots(recipe)):
        name = ingredient.get("name", "")
        normalized = _key(name)
        if normalized not in excluded:
            continue

        ancestry = tuple(ingredient.get("_adapt_ancestry", ()))
        if normalized in ancestry:
            blockers.add("substitution cycle involving %s" % name)
            continue

        alternatives = _matching_choices(ingredient, catalog)
        if not alternatives:
            blockers.add("no substitution for %s" % name)
        else:
            adaptable.append((ordinal, alternatives))

    return blockers, adaptable


def _search(recipe, excluded, catalog, available, selected, considered):
    blockers, adaptable = _excluded_state(recipe, excluded, catalog)

    if adaptable:
        ordinal, alternatives = adaptable[0]
        all_reasons = set(blockers)
        last_selected = selected

        for choice in alternatives:
            choice_id = choice.get("id")
            if choice_id is not None:
                considered.append(choice_id)

            branch = deepcopy(recipe)
            slots = list(_ingredient_slots(branch))
            ingredient_list, index, ingredient = slots[ordinal]
            ancestry = tuple(ingredient.get("_adapt_ancestry", ()))
            _apply_choice(branch, ingredient_list, index, choice, ancestry)

            branch_selected = selected + ([choice_id] if choice_id is not None else [])
            solved, used, reasons = _search(
                branch,
                excluded,
                catalog,
                available,
                branch_selected,
                considered,
            )
            if solved is not None and not blockers:
                return solved, used, set()

            all_reasons.update(reasons)
            last_selected = used

        return None, last_selected, all_reasons

    blockers.update(_unavailable_reasons(recipe, available))
    if blockers:
        return None, selected, blockers
    return recipe, selected, set()


def _remove_private_state(recipe):
    recipe.pop("_adapt_yield", None)
    recipe.pop("_adapt_equipment", None)
    recipe.pop("_adapt_warnings", None)
    for _, _, ingredient in _ingredient_slots(recipe):
        ingredient.pop("_adapt_ancestry", None)


def adapt(recipe, request, catalog):
    """Return a complete adapted-recipe result without mutating any input."""
    work = deepcopy(recipe)
    requested = deepcopy(request)
    ordered_catalog = _ordered_catalog(deepcopy(catalog))

    work["_adapt_yield"] = work["yield"]
    work["_adapt_equipment"] = list(_authored_equipment(work))
    work["_adapt_warnings"] = []

    excluded = {_key(item) for item in requested.get("excluded", [])}
    available = requested.get("available_equipment", []) or []
    considered = []

    solved, selected, reasons = _search(
        work,
        excluded,
        ordered_catalog,
        available,
        [],
        considered,
    )

    if solved is None:
        return {
            "possible": False,
            "recipe": None,
            "choices": considered,
            "warnings": [],
            "reasons": sorted(reasons),
        }

    target_yield = requested["target_yield"]
    scale = target_yield / solved["_adapt_yield"]
    for _, _, ingredient in _ingredient_slots(solved):
        ingredient["quantity"] *= scale

    warnings = sorted(set(solved["_adapt_warnings"]))
    solved["yield"] = target_yield
    solved["equipment"] = sorted(_effective_equipment(solved))
    _remove_private_state(solved)

    return {
        "possible": True,
        "recipe": solved,
        "choices": selected,
        "warnings": warnings,
        "reasons": [],
    }
