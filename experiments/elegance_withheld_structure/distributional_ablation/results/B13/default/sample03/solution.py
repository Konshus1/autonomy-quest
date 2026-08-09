"""Recipe adaptation using only the Python standard library."""

from copy import deepcopy


def print_original(recipe):
    """Return the recipe's authored text byte-for-byte."""
    return recipe["authored_text"]


def _key(value):
    return str(value).casefold()


def _component_values(components):
    if isinstance(components, list):
        return components
    if isinstance(components, dict):
        return components.values()
    return ()


def _recipe_nodes(recipe):
    """Yield the root and recipe-like component dictionaries."""
    yield recipe
    for component in _component_values(recipe.get("components", [])):
        if not isinstance(component, dict):
            continue
        if any(
            key in component
            for key in ("ingredients", "instructions", "equipment", "components")
        ):
            yield from _recipe_nodes(component)
        else:
            for value in component.values():
                if isinstance(value, dict) and any(
                    key in value
                    for key in (
                        "ingredients",
                        "instructions",
                        "equipment",
                        "components",
                    )
                ):
                    yield from _recipe_nodes(value)


def _ingredient_slots(recipe):
    for node in _recipe_nodes(recipe):
        ingredients = node.get("ingredients", []) or []
        for index, ingredient in enumerate(ingredients):
            yield ingredients, index, ingredient


def _scale_quantities(recipe, factor):
    for _, _, ingredient in _ingredient_slots(recipe):
        if "quantity" in ingredient:
            ingredient["quantity"] *= factor


def _apply_wording_changes(recipe, changes):
    for node in _recipe_nodes(recipe):
        instructions = node.get("instructions", []) or []
        for index, statement in enumerate(instructions):
            for change in changes:
                statement = statement.replace(change["old"], change["new"])
            instructions[index] = statement


def _equipment_delta(result):
    additions = result.get("equipment_additions")
    removals = result.get("equipment_removals")

    # Also accept natural equivalent spellings without changing the public output.
    if additions is None:
        additions = result.get("add_equipment", [])
    if removals is None:
        removals = result.get("remove_equipment", [])

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions = equipment.get(
            "additions", equipment.get("add", additions)
        )
        removals = equipment.get(
            "removals", equipment.get("remove", removals)
        )

    return additions or [], removals or []


def _apply_equipment_changes(recipe, result):
    additions, removals = _equipment_delta(result)
    equipment = set(recipe.get("equipment", []) or [])
    equipment.difference_update(removals)
    equipment.update(additions)
    recipe["equipment"] = sorted(equipment)


def _matching_choices(ingredient, catalog):
    targets = {
        _key(ingredient.get("name", "")),
        _key(ingredient.get("id", "")),
    }
    matches = []
    for catalog_index, choice in enumerate(catalog):
        if _key(choice.get("for", "")) in targets:
            matches.append(
                (choice.get("priority", 0), catalog_index, choice)
            )
    matches.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry[2] for entry in matches]


def _all_equipment(recipe):
    equipment = set()
    for node in _recipe_nodes(recipe):
        equipment.update(node.get("equipment", []) or [])
    return equipment


def _blocked_ingredients(recipe, excluded):
    blocked = []
    for slot_number, (container, index, ingredient) in enumerate(
        _ingredient_slots(recipe)
    ):
        if _key(ingredient.get("name", "")) in excluded:
            blocked.append((slot_number, container, index, ingredient))
    return blocked


def _is_cycle(ingredient):
    name = _key(ingredient.get("name", ""))
    return name in {
        _key(previous) for previous in ingredient.get("__lineage__", [])
    }


def _record_immediate_failures(
    recipe, blocked, catalog, available_equipment, failures
):
    for _, _, _, ingredient in blocked:
        name = ingredient.get("name", "")
        if _is_cycle(ingredient):
            failures.add("substitution cycle involving %s" % name)
        elif not _matching_choices(ingredient, catalog):
            failures.add("no substitution for %s" % name)

    for item in _all_equipment(recipe) - available_equipment:
        failures.add("equipment %s unavailable" % item)


def _apply_choice(recipe, slot_number, choice, yield_factor):
    slots = list(_ingredient_slots(recipe))
    container, index, old = slots[slot_number]
    result = choice.get("result", {})

    replacement = deepcopy(old)
    replacement["name"] = result["name"]
    if "quantity_factor" in result:
        replacement["quantity"] *= result["quantity_factor"]
    if "unit" in result:
        replacement["unit"] = result["unit"]
    if "preparation" in result:
        replacement["preparation"] = result["preparation"]

    lineage = list(old.get("__lineage__", []))
    lineage.append(old.get("name", ""))
    replacement["__lineage__"] = lineage
    container[index] = replacement

    additions = []
    for supplied in result.get("additional_ingredients", []) or []:
        ingredient = deepcopy(supplied)
        if "quantity" in ingredient:
            ingredient["quantity"] *= yield_factor
        ingredient["__lineage__"] = list(lineage)
        additions.append(ingredient)
    container[index + 1:index + 1] = additions

    _apply_wording_changes(
        recipe, result.get("wording_changes", []) or []
    )
    _apply_equipment_changes(recipe, result)


def _clean_adapted_recipe(recipe):
    for _, _, ingredient in _ingredient_slots(recipe):
        ingredient.pop("__lineage__", None)
    for node in _recipe_nodes(recipe):
        if "equipment" in node:
            node["equipment"] = sorted(
                set(node.get("equipment", []) or [])
            )
    return recipe


def adapt(recipe, request, catalog):
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    working = deepcopy(recipe)
    target_yield = request["target_yield"]
    yield_factor = target_yield / working["yield"]
    _scale_quantities(working, yield_factor)
    working["yield"] = target_yield

    excluded = {
        _key(name) for name in (request.get("excluded", []) or [])
    }
    available = set(request.get("available_equipment", []) or [])
    failures = set()
    considered = []

    def search(state, selected):
        blocked = _blocked_ingredients(state, excluded)

        if not blocked:
            missing = _all_equipment(state) - available
            if missing:
                for item in missing:
                    failures.add("equipment %s unavailable" % item)
                return None
            return _clean_adapted_recipe(state), selected

        _record_immediate_failures(
            state, blocked, catalog, available, failures
        )

        slot_number, _, _, ingredient = blocked[0]
        if _is_cycle(ingredient):
            return None

        choices = _matching_choices(ingredient, catalog)
        if not choices:
            return None

        for choice in choices:
            considered.append(choice["id"])
            branch = deepcopy(state)
            _apply_choice(branch, slot_number, choice, yield_factor)
            result = search(branch, selected + [choice["id"]])
            if result is not None:
                return result
        return None

    result = search(working, [])
    if result is not None:
        adapted_recipe, selected_choices = result
        return {
            "possible": True,
            "recipe": adapted_recipe,
            "choices": selected_choices,
            "warnings": [],
            "reasons": [],
        }

    return {
        "possible": False,
        "recipe": None,
        "choices": considered,
        "warnings": [],
        "reasons": sorted(failures),
    }
