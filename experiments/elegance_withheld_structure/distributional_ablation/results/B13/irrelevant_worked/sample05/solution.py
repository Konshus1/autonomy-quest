from copy import deepcopy
from fractions import Fraction


def _key(value):
    return str(value).casefold()


def _dicts(value):
    """Yield every dictionary in a JSON-shaped value in source order."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _dicts(child)


def _named_list_containers(recipe, field):
    return [node for node in _dicts(recipe) if isinstance(node.get(field), list)]


def print_original(recipe):
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    work = deepcopy(recipe)
    target_yield = request["target_yield"]
    scale = target_yield / work["yield"]

    excluded = {_key(name) for name in request.get("excluded", [])}
    available = set(request.get("available_equipment", []))

    choices = []
    warnings = set()
    reasons = set()

    ingredient_containers = _named_list_containers(work, "ingredients")
    instruction_containers = _named_list_containers(work, "instructions")
    equipment_containers = _named_list_containers(work, "equipment")

    # Freeze the deterministic catalog ordering once. Catalog position is the
    # tie-breaker for equal priorities.
    ordered_catalog = sorted(
        enumerate(catalog),
        key=lambda pair: (pair[1].get("priority", 0), pair[0]),
    )

    def catalog_choices(ingredient):
        name_key = _key(ingredient.get("name", ""))
        ingredient_id = ingredient.get("id")
        matches = []
        for _, choice in ordered_catalog:
            subject = choice.get("for")
            if _key(subject) == name_key or (
                ingredient_id is not None and subject == ingredient_id
            ):
                matches.append(choice)
        return matches

    def change_wording(changes):
        for change in changes:
            old = change.get("old", "")
            new = change.get("new", "")
            if not old:
                continue
            for container in instruction_containers:
                container["instructions"] = [
                    text.replace(old, new) for text in container["instructions"]
                ]

    def remove_equipment(names):
        removals = set(names)
        for container in equipment_containers:
            container["equipment"] = [
                item for item in container["equipment"] if item not in removals
            ]

    def add_equipment(names):
        # Substitution equipment is a recipe-wide requirement and is recorded
        # on the top-level recipe.
        top = work.setdefault("equipment", [])
        for name in names:
            if name not in top:
                top.append(name)

    def scaled_additional(raw):
        item = deepcopy(raw)
        if "quantity" in item:
            item["quantity"] = item["quantity"] * scale
        return item

    def resolve(ingredient, ancestry):
        name = ingredient.get("name", "")
        name_key = _key(name)

        if name_key not in excluded:
            return [ingredient]

        if name_key in ancestry:
            reasons.add("substitution cycle involving " + name)
            return []

        candidates = catalog_choices(ingredient)
        if not candidates:
            reasons.add("no substitution for " + name)
            return []

        choice = candidates[0]
        choices.append(choice["id"])
        result = choice.get("result", {})

        replacement = deepcopy(ingredient)
        replacement["name"] = result.get("name", replacement.get("name"))
        replacement["quantity"] = replacement["quantity"] * result.get(
            "quantity_factor", Fraction(1, 1)
        )
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        change_wording(result.get("wording_changes", []))

        removals = result.get(
            "equipment_removals", result.get("remove_equipment", [])
        )
        additions = result.get(
            "equipment_additions", result.get("add_equipment", [])
        )
        remove_equipment(removals)
        add_equipment(additions)

        next_ancestry = ancestry | {name_key}
        resolved = resolve(replacement, next_ancestry)
        for extra in result.get("additional_ingredients", []):
            resolved.extend(resolve(scaled_additional(extra), next_ancestry))
        return resolved

    # Scale authored ingredients before substitutions. Replacement factors then
    # operate on quantities already expressed for the requested yield.
    for container in ingredient_containers:
        scaled = []
        for original in container["ingredients"]:
            item = deepcopy(original)
            item["quantity"] = item["quantity"] * scale
            scaled.extend(resolve(item, set()))
        container["ingredients"] = scaled

    work["yield"] = target_yield

    # Validate the final graph as a safeguard against malformed catalog data.
    for container in _named_list_containers(work, "ingredients"):
        for ingredient in container["ingredients"]:
            if _key(ingredient.get("name", "")) in excluded:
                reasons.add("no substitution for " + ingredient.get("name", ""))

    for container in _named_list_containers(work, "equipment"):
        container["equipment"] = sorted(set(container["equipment"]))
        for item in container["equipment"]:
            if item not in available:
                reasons.add("equipment " + item + " unavailable")

    ordered_reasons = sorted(reasons)
    return {
        "possible": not ordered_reasons,
        "recipe": work if not ordered_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": ordered_reasons,
    }
