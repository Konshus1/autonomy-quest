from copy import deepcopy
from fractions import Fraction


def _norm(value):
    return str(value).strip().casefold()


def _as_fraction(value):
    if isinstance(value, Fraction):
        return value
    return Fraction(value)


def _ingredient_lists(value):
    """Yield every ingredient list in a recipe and its components."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "ingredients" and isinstance(child, list):
                yield child
            else:
                yield from _ingredient_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _ingredient_lists(child)


def _rewrite_instructions(value, old, new):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "instructions" and isinstance(child, list):
                value[key] = [
                    text.replace(old, new) if isinstance(text, str) else text
                    for text in child
                ]
            else:
                _rewrite_instructions(child, old, new)
    elif isinstance(value, list):
        for child in value:
            _rewrite_instructions(child, old, new)


def _messages(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _equipment_changes(result):
    additions = result.get("equipment_additions", result.get("add_equipment", []))
    removals = result.get("equipment_removals", result.get("remove_equipment", []))

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions = equipment.get("add", additions)
        removals = equipment.get("remove", removals)

    return list(additions or []), list(removals or [])


def adapt(recipe, request, catalog):
    """Return a fully adapted copy of recipe or all applicable failure reasons."""
    adapted = deepcopy(recipe)
    excluded = {_norm(name) for name in request.get("excluded", [])}
    available = {_norm(name) for name in request.get("available_equipment", [])}

    ranked_catalog = []
    for index, choice in enumerate(catalog):
        ranked_catalog.append(
            (choice.get("priority", 0), index, choice)
        )
    ranked_catalog.sort(key=lambda entry: (entry[0], entry[1]))

    choices = []
    warnings = []
    reasons = []
    effective_yield = _as_fraction(adapted["yield"])

    equipment_by_name = {
        _norm(name): name for name in adapted.get("equipment", [])
    }

    def candidates_for(ingredient):
        name = _norm(ingredient.get("name", ""))
        ingredient_id = _norm(ingredient.get("id", ""))
        matches = []
        for priority, index, choice in ranked_catalog:
            target = _norm(choice.get("for", ""))
            if target == name or (ingredient_id and target == ingredient_id):
                matches.append((priority, index, choice))
        return matches

    def apply_choice(ingredient, choice, containing_list, ancestry):
        nonlocal effective_yield

        result = choice.get("result", {})
        choices.append(choice["id"])
        warnings.extend(_messages(choice.get("warnings")))
        warnings.extend(_messages(result.get("warnings")))

        if "quantity_factor" in result:
            ingredient["quantity"] = (
                _as_fraction(ingredient["quantity"])
                * _as_fraction(result["quantity_factor"])
            )
        if "name" in result:
            ingredient["name"] = result["name"]
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        for change in result.get("wording_changes", []) or []:
            if isinstance(change, dict):
                old = str(change.get("old", ""))
                new = str(change.get("new", ""))
            else:
                old, new = map(str, change)
            _rewrite_instructions(adapted, old, new)

        additions, removals = _equipment_changes(result)
        for name in removals:
            equipment_by_name.pop(_norm(name), None)
        for name in additions:
            equipment_by_name[_norm(name)] = name

        if "yield_factor" in result:
            effective_yield *= _as_fraction(result["yield_factor"])
        if "yield_change" in result:
            effective_yield *= _as_fraction(result["yield_change"])
        if "yield" in result:
            effective_yield = _as_fraction(result["yield"])

        new_items = []
        for additional in result.get("additional_ingredients", []) or []:
            copied = deepcopy(additional)
            containing_list.append(copied)
            new_items.append((copied, (_norm(copied.get("name", "")),)))

        return new_items

    pending = []
    for ingredients in list(_ingredient_lists(adapted)):
        for ingredient in list(ingredients):
            pending.append(
                (ingredient, ingredients, (_norm(ingredient.get("name", "")),))
            )

    position = 0
    while position < len(pending):
        ingredient, containing_list, ancestry = pending[position]
        position += 1

        while _norm(ingredient.get("name", "")) in excluded:
            matches = candidates_for(ingredient)
            if not matches:
                reasons.append(
                    "no substitution for " + str(ingredient.get("name", ""))
                )
                break

            choice = matches[0][2]
            introduced = apply_choice(
                ingredient, choice, containing_list, ancestry
            )
            pending.extend(
                (item, containing_list, item_ancestry)
                for item, item_ancestry in introduced
            )

            replacement_name = str(ingredient.get("name", ""))
            replacement_key = _norm(replacement_name)
            if replacement_key in excluded:
                if replacement_key in ancestry:
                    reasons.append(
                        "substitution cycle involving " + replacement_name
                    )
                    break
                ancestry = ancestry + (replacement_key,)
            else:
                break

    adapted["equipment"] = sorted(equipment_by_name.values())
    for name in adapted["equipment"]:
        if _norm(name) not in available:
            reasons.append("equipment " + str(name) + " unavailable")

    if not reasons:
        target_yield = _as_fraction(request["target_yield"])
        factor = target_yield / effective_yield
        for ingredients in _ingredient_lists(adapted):
            for ingredient in ingredients:
                ingredient["quantity"] = (
                    _as_fraction(ingredient["quantity"]) * factor
                )
        adapted["yield"] = target_yield

    reasons = sorted(set(reasons))
    return {
        "possible": not reasons,
        "recipe": adapted if not reasons else None,
        "choices": choices,
        "warnings": sorted(set(warnings)),
        "reasons": reasons,
    }


def print_original(recipe):
    """Return the author's original recipe text without reconstruction."""
    return recipe["authored_text"]
