from copy import deepcopy


def _key(value):
    return str(value).casefold()


def _walk_component_nodes(recipe):
    """Yield the root and recipe-like dictionaries nested in components."""
    yield recipe

    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            if any(
                key in value
                for key in ("ingredients", "equipment", "instructions", "components")
            ):
                yield value
                if "components" in value:
                    yield from walk(value["components"])
            else:
                for item in value.values():
                    yield from walk(item)

    if "components" in recipe:
        yield from walk(recipe["components"])


def _equipment_delta(result):
    additions = []
    removals = []

    for key in ("equipment_additions", "equipment_add", "add_equipment"):
        additions.extend(result.get(key, ()) or ())
    for key in ("equipment_removals", "equipment_remove", "remove_equipment"):
        removals.extend(result.get(key, ()) or ())

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions.extend(equipment.get("add", ()) or ())
        additions.extend(equipment.get("additions", ()) or ())
        removals.extend(equipment.get("remove", ()) or ())
        removals.extend(equipment.get("removals", ()) or ())

    return additions, removals


def adapt(recipe, request, catalog):
    adapted = deepcopy(recipe)
    excluded = {_key(name) for name in request.get("excluded", ())}
    available = set(request.get("available_equipment", ()))

    by_ingredient = {}
    for position, choice in enumerate(catalog):
        by_ingredient.setdefault(_key(choice["for"]), []).append(
            (choice["priority"], position, choice)
        )
    for candidates in by_ingredient.values():
        candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))

    chosen_ids = []
    warnings = set()
    reasons = set()
    wording_changes = []

    target_yield = request["target_yield"]
    scale = target_yield / recipe["yield"]
    nodes = list(_walk_component_nodes(adapted))

    for node_number, node in enumerate(nodes):
        equipment = set(node.get("equipment", ()) or ())
        equipment_was_present = "equipment" in node
        equipment_was_changed = False
        ingredients = node.get("ingredients")

        if isinstance(ingredients, list):
            pending = [(deepcopy(ingredient), ()) for ingredient in ingredients]
            resolved = []
            cursor = 0

            while cursor < len(pending):
                ingredient, ancestry = pending[cursor]
                cursor += 1

                while _key(ingredient["name"]) in excluded:
                    name = ingredient["name"]
                    name_key = _key(name)

                    if name_key in ancestry:
                        reasons.add(f"substitution cycle involving {name}")
                        ingredient = None
                        break

                    candidates = by_ingredient.get(name_key)
                    if not candidates:
                        reasons.add(f"no substitution for {name}")
                        ingredient = None
                        break

                    choice = candidates[0][2]
                    chosen_ids.append(choice["id"])
                    result = choice["result"]
                    next_ancestry = ancestry + (name_key,)

                    ingredient["name"] = result["name"]
                    if "quantity_factor" in result:
                        ingredient["quantity"] *= result["quantity_factor"]
                    if "unit" in result:
                        ingredient["unit"] = result["unit"]
                    if "preparation" in result:
                        ingredient["preparation"] = result["preparation"]

                    additions, removals = _equipment_delta(result)
                    if additions or removals:
                        equipment_was_changed = True
                    equipment.update(additions)
                    equipment.difference_update(removals)

                    for change in result.get("wording_changes", ()) or ():
                        wording_changes.append((change["old"], change["new"]))

                    for additional in result.get("additional_ingredients", ()) or ():
                        pending.append((deepcopy(additional), next_ancestry))

                    ancestry = next_ancestry

                if ingredient is not None:
                    ingredient["quantity"] *= scale
                    resolved.append(ingredient)

            node["ingredients"] = resolved

        if equipment_was_present or equipment_was_changed:
            node["equipment"] = sorted(equipment)

        for item in equipment:
            if item not in available:
                reasons.add(f"equipment {item} unavailable")

        if node_number and "yield" in node:
            node["yield"] *= scale

    # Apply changes in choice order so intentional chained rewrites work.
    for node in nodes:
        instructions = node.get("instructions")
        if isinstance(instructions, list):
            rewritten = []
            for authored in instructions:
                text = authored
                for old, new in wording_changes:
                    text = text.replace(old, new)
                rewritten.append(text)
            node["instructions"] = rewritten

    adapted["yield"] = target_yield

    return {
        "possible": not reasons,
        "recipe": adapted if not reasons else None,
        "choices": chosen_ids,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }


def print_original(recipe):
    return recipe["authored_text"]
