"""Recipe adaptation using only the Python standard library."""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the original authored recipe byte-for-byte."""
    return recipe["authored_text"]


def _key(value):
    return str(value).casefold()


def _ingredient_lists(recipe):
    lists = []

    def visit(section):
        if not isinstance(section, dict):
            return

        ingredients = section.get("ingredients")
        if isinstance(ingredients, list):
            lists.append(ingredients)

        components = section.get("components", [])
        if isinstance(components, dict):
            components = components.values()
        if isinstance(components, (list, tuple)):
            for component in components:
                visit(component)

    visit(recipe)
    return lists


def _rewrite_instructions(recipe, old, new):
    def visit(section):
        if not isinstance(section, dict):
            return

        instructions = section.get("instructions")
        if isinstance(instructions, list):
            section["instructions"] = [
                text.replace(old, new) if isinstance(text, str) else text
                for text in instructions
            ]

        components = section.get("components", [])
        if isinstance(components, dict):
            components = components.values()
        if isinstance(components, (list, tuple)):
            for component in components:
                visit(component)

    visit(recipe)


def _matching_choices(catalog, ingredient):
    name = _key(ingredient.get("name", ""))
    ingredient_id = _key(ingredient.get("id", ""))
    matches = []

    for index, choice in enumerate(catalog):
        subject = _key(choice.get("for", ""))
        if subject == name or (ingredient_id and subject == ingredient_id):
            matches.append((choice.get("priority", 0), index, choice))

    matches.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in matches]


def _equipment_change(result, kind):
    if kind == "add":
        names = ("equipment_additions", "add_equipment", "equipment_add")
    else:
        names = ("equipment_removals", "remove_equipment", "equipment_remove")

    for name in names:
        if name in result:
            value = result[name]
            return [value] if isinstance(value, str) else list(value or [])

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        value = equipment.get(kind, [])
        return [value] if isinstance(value, str) else list(value or [])

    return []


def _record_warnings(result, warnings):
    for key in ("warning", "warnings"):
        value = result.get(key)
        if not value:
            continue
        if isinstance(value, str):
            warnings.add(value)
        else:
            warnings.update(value)


def adapt(recipe, request, catalog):
    """Return a fully adapted recipe or every applicable failure reason."""
    work = deepcopy(recipe)
    excluded = {_key(name) for name in request.get("excluded", [])}
    equipment = set(work.get("equipment", []))
    choices = []
    warnings = set()
    reasons = set()

    pending = []
    for ingredients in _ingredient_lists(work):
        for ingredient in ingredients:
            pending.append((ingredients, ingredient, ()))

    while pending:
        container, ingredient, lineage = pending.pop(0)
        name = str(ingredient.get("name", ""))
        normalized_name = _key(name)

        if normalized_name not in excluded:
            continue

        if normalized_name in lineage:
            reasons.add("substitution cycle involving " + name)
            continue

        options = _matching_choices(catalog, ingredient)
        if not options:
            reasons.add("no substitution for " + name)
            continue

        choice = options[0]
        choices.append(choice["id"])
        result = choice.get("result", {})
        replacement = deepcopy(ingredient)

        if "name" in result:
            replacement["name"] = result["name"]
        if "id" in result:
            replacement["id"] = result["id"]
        if "quantity_factor" in result:
            replacement["quantity"] = (
                Fraction(replacement["quantity"])
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        position = next(
            index for index, item in enumerate(container) if item is ingredient
        )
        container[position] = replacement

        for change in result.get("wording_changes", []):
            _rewrite_instructions(work, change["old"], change["new"])

        equipment.difference_update(_equipment_change(result, "remove"))
        equipment.update(_equipment_change(result, "add"))

        if "yield" in result:
            work["yield"] = Fraction(result["yield"])
        if "yield_factor" in result:
            work["yield"] = (
                Fraction(work["yield"]) * Fraction(result["yield_factor"])
            )

        _record_warnings(result, warnings)

        next_lineage = lineage + (normalized_name,)
        pending.append((container, replacement, next_lineage))

        additional = result.get(
            "additional_ingredients", result.get("additional", [])
        )
        for extra in additional:
            extra = deepcopy(extra)
            container.append(extra)
            pending.append((container, extra, next_lineage))

    available = set(request.get("available_equipment", []))
    for item in equipment - available:
        reasons.add("equipment " + str(item) + " unavailable")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    target_yield = Fraction(request["target_yield"])
    scale = target_yield / Fraction(work["yield"])

    for ingredients in _ingredient_lists(work):
        for ingredient in ingredients:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    work["yield"] = target_yield
    work["equipment"] = sorted(equipment)

    return {
        "possible": True,
        "recipe": work,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
