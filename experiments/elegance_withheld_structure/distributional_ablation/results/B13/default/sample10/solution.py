from copy import deepcopy


def _normalized(value):
    return str(value).strip().casefold()


def _component_children(node):
    if isinstance(node, dict):
        components = node.get("components", [])
        if isinstance(components, dict):
            return components.values()
        if isinstance(components, list):
            return components
    return ()


def _scale_existing(node, factor):
    if not isinstance(node, dict):
        return

    ingredients = node.get("ingredients")
    if isinstance(ingredients, list):
        for ingredient in ingredients:
            if isinstance(ingredient, dict) and "quantity" in ingredient:
                ingredient["quantity"] *= factor

    for component in _component_children(node):
        _scale_existing(component, factor)


def _walk_recipe_dicts(node):
    if not isinstance(node, dict):
        return
    yield node
    for component in _component_children(node):
        yield from _walk_recipe_dicts(component)


def _catalog_choices(catalog):
    if isinstance(catalog, list):
        return catalog
    if isinstance(catalog, dict):
        choices = catalog.get("choices", [])
        return choices if isinstance(choices, list) else []
    return []


def _choice_matches(choice, ingredient):
    target = choice.get("for")
    targets = target if isinstance(target, list) else [target]
    names = {
        _normalized(ingredient.get("name", "")),
        _normalized(ingredient.get("id", "")),
    }
    return any(_normalized(item) in names for item in targets if item is not None)


def _wording_pairs(result):
    for change in result.get("wording_changes", []):
        if isinstance(change, dict) and "old" in change and "new" in change:
            yield str(change["old"]), str(change["new"])
        elif isinstance(change, (list, tuple)) and len(change) == 2:
            yield str(change[0]), str(change[1])


def _equipment_changes(result):
    additions = result.get("equipment_additions", result.get("add_equipment", []))
    removals = result.get("equipment_removals", result.get("remove_equipment", []))

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions = equipment.get("additions", equipment.get("add", additions))
        removals = equipment.get("removals", equipment.get("remove", removals))

    if isinstance(additions, str):
        additions = [additions]
    if isinstance(removals, str):
        removals = [removals]
    return list(additions or []), list(removals or [])


def adapt(recipe, request, catalog):
    adapted = deepcopy(recipe)
    choices = []
    warnings = []
    reasons = set()
    wording_changes = []
    equipment_additions = []
    equipment_removals = set()

    original_yield = adapted["yield"]
    target_yield = request["target_yield"]
    scale = target_yield / original_yield
    _scale_existing(adapted, scale)
    adapted["yield"] = target_yield

    excluded = {_normalized(name) for name in request.get("excluded", [])}
    indexed_catalog = list(enumerate(_catalog_choices(catalog)))

    def select_choice(ingredient):
        matches = [
            (choice.get("priority", 0), index, choice)
            for index, choice in indexed_catalog
            if isinstance(choice, dict) and _choice_matches(choice, ingredient)
        ]
        if not matches:
            return None
        return min(matches, key=lambda item: (item[0], item[1]))[2]

    def process_ingredient(ingredient, ancestry=()):
        current = deepcopy(ingredient)
        name = str(current.get("name", ""))
        key = _normalized(name)

        if key not in excluded:
            return [current]

        if key in ancestry:
            reasons.add("substitution cycle involving " + name)
            return [current]

        choice = select_choice(current)
        if choice is None:
            reasons.add("no substitution for " + name)
            return [current]

        choices.append(choice["id"])
        result = choice.get("result", {})
        replacement = deepcopy(current)
        replacement["name"] = result["name"]
        replacement["quantity"] *= result.get("quantity_factor", 1)

        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        wording_changes.extend(_wording_pairs(result))
        additions, removals = _equipment_changes(result)
        equipment_additions.extend(additions)
        equipment_removals.update(_normalized(item) for item in removals)

        next_ancestry = ancestry + (key,)
        output = process_ingredient(replacement, next_ancestry)

        for additional in result.get("additional_ingredients", []):
            added = deepcopy(additional)
            if "quantity" in added:
                added["quantity"] *= scale
            output.extend(process_ingredient(added, next_ancestry))

        return output

    for section in _walk_recipe_dicts(adapted):
        ingredients = section.get("ingredients")
        if isinstance(ingredients, list):
            processed = []
            for ingredient in ingredients:
                processed.extend(process_ingredient(ingredient))
            section["ingredients"] = processed

    root_equipment = adapted.setdefault("equipment", [])
    root_equipment.extend(deepcopy(equipment_additions))

    available_by_name = {}
    for item in request.get("available_equipment", []):
        available_by_name.setdefault(_normalized(item), item)

    for section in _walk_recipe_dicts(adapted):
        instructions = section.get("instructions")
        if isinstance(instructions, list):
            rewritten = []
            for instruction in instructions:
                text = instruction
                for old, new in wording_changes:
                    text = text.replace(old, new)
                rewritten.append(text)
            section["instructions"] = rewritten

        equipment = section.get("equipment")
        if isinstance(equipment, list):
            final_equipment = {}
            for item in equipment:
                key = _normalized(item)
                if key in equipment_removals:
                    continue
                if key in available_by_name:
                    final_equipment[key] = available_by_name[key]
                else:
                    final_equipment[key] = item
                    reasons.add("equipment " + str(item) + " unavailable")
            section["equipment"] = sorted(final_equipment.values(), key=str)

        for ingredient in section.get("ingredients", []):
            name = str(ingredient.get("name", ""))
            if _normalized(name) in excluded:
                key = _normalized(name)
                if key not in {
                    _normalized(reason[len("no substitution for "):])
                    for reason in reasons
                    if reason.startswith("no substitution for ")
                } and not any(
                    reason.startswith("substitution cycle involving ")
                    and _normalized(reason[len("substitution cycle involving "):]) == key
                    for reason in reasons
                ):
                    reasons.add("no substitution for " + name)

    ordered_reasons = sorted(reasons)
    return {
        "possible": not ordered_reasons,
        "recipe": adapted if not ordered_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": ordered_reasons,
    }


def print_original(recipe):
    return recipe["authored_text"]
