"""Recipe adaptation using only Python's standard library."""

from copy import deepcopy


def _normal(value):
    return str(value).casefold()


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def print_original(recipe):
    """Return the recipe's original authored text exactly as stored."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    target_yield = request["target_yield"]
    available = set(request.get("available_equipment", []))
    excluded = {_normal(name) for name in request.get("excluded", [])}

    ranked_catalog = [
        choice
        for _, choice in sorted(
            enumerate(catalog),
            key=lambda item: (item[1]["priority"], item[0]),
        )
    ]

    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    yield_state = [adapted["yield"]]

    def catalog_choice(ingredient):
        names = {_normal(ingredient.get("name", ""))}
        if "id" in ingredient:
            names.add(_normal(ingredient["id"]))
        for choice in ranked_catalog:
            if _normal(choice["for"]) in names:
                return choice
        return None

    def change_equipment(owner, result):
        equipment = list(owner.get("equipment", []))
        removals = _as_list(
            result.get("equipment_removals", result.get("remove_equipment"))
        )
        additions = _as_list(
            result.get("equipment_additions", result.get("add_equipment"))
        )
        removal_keys = {_normal(item) for item in removals}
        equipment = [
            item for item in equipment if _normal(item) not in removal_keys
        ]
        existing = {_normal(item) for item in equipment}
        for item in additions:
            if _normal(item) not in existing:
                equipment.append(item)
                existing.add(_normal(item))
        if additions or removals or "equipment" in owner:
            owner["equipment"] = equipment

    def record_wording_changes(result):
        changes = result.get("wording_changes", [])
        if isinstance(changes, dict):
            if "old" in changes and "new" in changes:
                changes = [changes]
            else:
                changes = [
                    {"old": old, "new": new}
                    for old, new in changes.items()
                ]
        for change in changes:
            wording_changes.append((change["old"], change["new"]))

    def change_yield(result):
        if "yield" in result:
            yield_state[0] = result["yield"]
        if "yield_factor" in result:
            yield_state[0] *= result["yield_factor"]

    def resolve_ingredient(ingredient, owner, trail):
        current = deepcopy(ingredient)
        name = current["name"]
        key = _normal(name)

        if key not in excluded:
            return [current]

        if key in trail:
            reasons.add("substitution cycle involving " + str(name))
            return [current]

        choice = catalog_choice(current)
        if choice is None:
            reasons.add("no substitution for " + str(name))
            return [current]

        choices.append(choice["id"])
        result = choice["result"]
        next_trail = trail | {key}

        replacement = deepcopy(current)
        replacement["name"] = result["name"]
        if "quantity_factor" in result:
            replacement["quantity"] *= result["quantity_factor"]
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        change_equipment(owner, result)
        record_wording_changes(result)
        change_yield(result)

        resolved = resolve_ingredient(replacement, owner, next_trail)
        extras = result.get(
            "additional_ingredients", result.get("additional_ingredient", [])
        )
        for extra in _as_list(extras):
            resolved.extend(resolve_ingredient(extra, owner, next_trail))
        return resolved

    def visit_containers(value):
        if isinstance(value, dict):
            ingredients = value.get("ingredients")
            if isinstance(ingredients, list):
                resolved = []
                for ingredient in ingredients:
                    if isinstance(ingredient, dict) and "name" in ingredient:
                        resolved.extend(resolve_ingredient(ingredient, value, set()))
                    else:
                        resolved.append(deepcopy(ingredient))
                value["ingredients"] = resolved

            components = value.get("components")
            if isinstance(components, (list, dict)):
                visit_containers(components)
        elif isinstance(value, list):
            for item in value:
                visit_containers(item)

    visit_containers(adapted)

    scale = target_yield / yield_state[0]

    def finalize(value):
        if isinstance(value, dict):
            ingredients = value.get("ingredients")
            if isinstance(ingredients, list):
                for ingredient in ingredients:
                    if not isinstance(ingredient, dict) or "name" not in ingredient:
                        continue
                    ingredient["quantity"] *= scale
                    if _normal(ingredient["name"]) in excluded:
                        name = str(ingredient["name"])
                        cycle_reason = "substitution cycle involving " + name
                        missing_reason = "no substitution for " + name
                        if cycle_reason not in reasons and missing_reason not in reasons:
                            reasons.add(missing_reason)

            instructions = value.get("instructions")
            if isinstance(instructions, list):
                rewritten = []
                for instruction in instructions:
                    text = instruction
                    for old, new in wording_changes:
                        text = text.replace(old, new)
                    rewritten.append(text)
                value["instructions"] = rewritten

            equipment = value.get("equipment")
            if isinstance(equipment, list):
                value["equipment"] = sorted(set(equipment))
                for item in value["equipment"]:
                    if item not in available:
                        reasons.add("equipment " + str(item) + " unavailable")

            components = value.get("components")
            if isinstance(components, (list, dict)):
                finalize(components)
        elif isinstance(value, list):
            for item in value:
                finalize(item)

    finalize(adapted)
    adapted["yield"] = target_yield

    ordered_warnings = sorted(warnings)
    ordered_reasons = sorted(reasons)
    if ordered_reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": ordered_warnings,
            "reasons": ordered_reasons,
        }

    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": ordered_warnings,
        "reasons": [],
    }
