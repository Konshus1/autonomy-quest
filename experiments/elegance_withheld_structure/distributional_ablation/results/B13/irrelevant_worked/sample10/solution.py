from copy import deepcopy
from fractions import Fraction

__all__ = ["adapt", "print_original"]


def _normalized(value):
    return str(value).casefold()


def _recipe_containers(recipe):
    """Yield the recipe and component dictionaries containing ingredients."""

    def visit(node):
        if isinstance(node, list):
            for item in node:
                yield from visit(item)
        elif isinstance(node, dict):
            if isinstance(node.get("ingredients"), list):
                yield node
                yield from visit(node.get("components", []))
            else:
                for value in node.values():
                    yield from visit(value)

    yield from visit(recipe)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _equipment_change(result, additions):
    if additions:
        value = result.get(
            "equipment_additions", result.get("add_equipment")
        )
        nested_key = "add"
    else:
        value = result.get(
            "equipment_removals", result.get("remove_equipment")
        )
        nested_key = "remove"

    if value is None and isinstance(result.get("equipment"), dict):
        value = result["equipment"].get(nested_key)

    return _as_list(value)


def print_original(recipe):
    """Return the original authored recipe byte-for-byte as a string."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    excluded = {
        _normalized(name) for name in request.get("excluded", [])
    }
    available = set(request.get("available_equipment", []))
    target_yield = request["target_yield"]

    indexed_catalog = list(enumerate(catalog))
    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    equipment = set(adapted.get("equipment", []))
    effective_yield = adapted["yield"]

    def matching_choices(ingredient):
        ingredient_name = _normalized(ingredient.get("name", ""))
        matches = [
            (position, choice)
            for position, choice in indexed_catalog
            if _normalized(choice.get("for", "")) == ingredient_name
        ]
        matches.sort(
            key=lambda item: (item[1].get("priority", 0), item[0])
        )
        return matches

    def add_warnings(value):
        for warning in _as_list(value):
            warnings.add(str(warning))

    def resolve(ingredient, ancestry=()):
        nonlocal effective_yield

        introduced = []
        while _normalized(ingredient.get("name", "")) in excluded:
            display_name = ingredient.get("name", "")
            key = _normalized(display_name)

            if key in ancestry:
                reasons.add(
                    f"substitution cycle involving {display_name}"
                )
                return [ingredient] + introduced

            matches = matching_choices(ingredient)
            if not matches:
                reasons.add(f"no substitution for {display_name}")
                return [ingredient] + introduced

            _, choice = matches[0]
            result = choice.get("result") or {}
            choices.append(choice["id"])
            next_ancestry = ancestry + (key,)

            ingredient["quantity"] *= result.get(
                "quantity_factor", Fraction(1)
            )
            for field in ("name", "unit", "preparation"):
                if field in result:
                    ingredient[field] = result[field]

            equipment.difference_update(
                _equipment_change(result, additions=False)
            )
            equipment.update(
                _equipment_change(result, additions=True)
            )

            for change in result.get("wording_changes", []):
                wording_changes.append((change["old"], change["new"]))

            add_warnings(choice.get("warning"))
            add_warnings(choice.get("warnings"))
            add_warnings(result.get("warning"))
            add_warnings(result.get("warnings"))

            if "yield_factor" in result:
                effective_yield *= result["yield_factor"]
            if "yield" in result:
                effective_yield = result["yield"]

            for extra in deepcopy(
                result.get("additional_ingredients", [])
            ):
                introduced.extend(resolve(extra, next_ancestry))

            ancestry = next_ancestry

        return [ingredient] + introduced

    containers = list(_recipe_containers(adapted))

    for container in containers:
        resolved = []
        for ingredient in container.get("ingredients", []):
            resolved.extend(resolve(ingredient))
        container["ingredients"] = resolved

    for old, new in wording_changes:
        for container in containers:
            instructions = container.get("instructions")
            if isinstance(instructions, list):
                container["instructions"] = [
                    text.replace(old, new)
                    if isinstance(text, str)
                    else text
                    for text in instructions
                ]

    scale = target_yield / effective_yield
    for container in containers:
        for ingredient in container.get("ingredients", []):
            ingredient["quantity"] *= scale

        if (
            container is not adapted
            and isinstance(container.get("equipment"), list)
        ):
            component_equipment = set(container["equipment"])
            container["equipment"] = sorted(component_equipment)
            for item in component_equipment - available:
                reasons.add(f"equipment {item} unavailable")

    adapted["yield"] = target_yield
    adapted["equipment"] = sorted(equipment)

    for item in equipment - available:
        reasons.add(f"equipment {item} unavailable")

    ordered_reasons = sorted(reasons)
    return {
        "possible": not ordered_reasons,
        "recipe": adapted if not ordered_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": ordered_reasons,
    }
