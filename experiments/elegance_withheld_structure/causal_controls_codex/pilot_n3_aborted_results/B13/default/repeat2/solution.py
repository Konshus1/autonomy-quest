"""Recipe adaptation using only the Python standard library."""

from copy import deepcopy
from fractions import Fraction


def _key(value):
    return str(value).strip().casefold()


def _component_nodes(value):
    """Yield recipe/component dictionaries recursively."""
    if isinstance(value, list):
        for item in value:
            yield from _component_nodes(item)
        return

    if not isinstance(value, dict):
        return

    if any(key in value for key in ("ingredients", "instructions", "equipment")):
        yield value

    components = value.get("components")
    if isinstance(components, list):
        for component in components:
            yield from _component_nodes(component)
    elif isinstance(components, dict):
        if any(key in components for key in ("ingredients", "instructions", "equipment")):
            yield from _component_nodes(components)
        else:
            for component in components.values():
                yield from _component_nodes(component)


def _matching_choices(ingredient, catalog):
    identities = {
        _key(ingredient.get("name", "")),
        _key(ingredient.get("id", "")),
    }
    matches = []

    for position, choice in enumerate(catalog):
        subjects = choice.get("for", "")
        if not isinstance(subjects, (list, tuple, set)):
            subjects = [subjects]

        if identities.intersection(_key(subject) for subject in subjects):
            matches.append((choice.get("priority", 0), position, choice))

    matches.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in matches]


def _equipment_changes(result):
    additions = []
    removals = []

    for key in ("equipment_additions", "equipment_add", "add_equipment"):
        additions.extend(result.get(key, []))
    for key in ("equipment_removals", "equipment_remove", "remove_equipment"):
        removals.extend(result.get(key, []))

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions.extend(equipment.get("add", equipment.get("additions", [])))
        removals.extend(equipment.get("remove", equipment.get("removals", [])))
    elif isinstance(equipment, list):
        additions.extend(equipment)

    return additions, removals


def _apply_equipment_changes(values, additions, removals):
    removed = {_key(value) for value in removals}
    result = {str(value) for value in values if _key(value) not in removed}
    result.update(str(value) for value in additions)
    return sorted(result)


def _additional_ingredient(value, serial):
    if isinstance(value, str):
        return {
            "id": "additional-{}".format(serial),
            "name": value,
            "quantity": Fraction(0),
            "unit": "",
            "preparation": "",
        }

    ingredient = deepcopy(value)
    ingredient.setdefault("id", "additional-{}".format(serial))
    ingredient.setdefault("name", "")
    ingredient.setdefault("quantity", Fraction(0))
    ingredient.setdefault("unit", "")
    ingredient.setdefault("preparation", "")
    return ingredient


def adapt(recipe, request, catalog):
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    excluded = {_key(value) for value in request.get("excluded", [])}
    available = {_key(value) for value in request.get("available_equipment", [])}

    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    equipment_additions = []
    equipment_removals = []
    yield_factor = Fraction(1)
    yield_override = None
    serial = 0

    def resolve(ingredient, ancestry):
        nonlocal yield_factor, yield_override, serial

        name = str(ingredient.get("name", ""))
        normalized_name = _key(name)
        if normalized_name not in excluded:
            return ingredient, []

        if normalized_name in ancestry:
            reasons.add("substitution cycle involving {}".format(name))
            return None, []

        candidates = _matching_choices(ingredient, catalog)
        if not candidates:
            reasons.add("no substitution for {}".format(name))
            return None, []

        choice = candidates[0]
        choices.append(choice.get("id"))
        result = choice.get("result") or {}

        replacement = deepcopy(ingredient)
        if "name" in result:
            replacement["name"] = result["name"]
        replacement["quantity"] = (
            Fraction(replacement.get("quantity", 0))
            * Fraction(result.get("quantity_factor", 1))
        )

        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        for change in result.get("wording_changes", []):
            if isinstance(change, dict):
                wording_changes.append(
                    (str(change.get("old", "")), str(change.get("new", "")))
                )
            elif isinstance(change, (list, tuple)) and len(change) == 2:
                wording_changes.append((str(change[0]), str(change[1])))

        additions, removals = _equipment_changes(result)
        equipment_additions.extend(additions)
        equipment_removals.extend(removals)

        if "yield_factor" in result:
            yield_factor *= Fraction(result["yield_factor"])
        if "yield" in result:
            yield_override = Fraction(result["yield"])

        replacement, nested = resolve(
            replacement, ancestry + (normalized_name,)
        )

        introduced = list(nested)
        for value in result.get("additional_ingredients", []):
            serial += 1
            extra = _additional_ingredient(value, serial)
            extra, extra_introduced = resolve(
                extra, ancestry + (normalized_name,)
            )
            if extra is not None:
                introduced.append(extra)
            introduced.extend(extra_introduced)

        return replacement, introduced

    nodes = list(_component_nodes(adapted))

    for node in nodes:
        ingredients = node.get("ingredients")
        if not isinstance(ingredients, list):
            continue

        resolved_ingredients = []
        for ingredient in ingredients:
            resolved, introduced = resolve(deepcopy(ingredient), ())
            if resolved is not None:
                resolved_ingredients.append(resolved)
            resolved_ingredients.extend(introduced)
        node["ingredients"] = resolved_ingredients

    for node in nodes:
        instructions = node.get("instructions")
        if not isinstance(instructions, list):
            continue

        edited = []
        for instruction in instructions:
            text = instruction
            for old, new in wording_changes:
                if old:
                    text = text.replace(old, new)
            edited.append(text)
        node["instructions"] = edited

    required_equipment = set()
    for node in nodes:
        if "equipment" not in node:
            continue
        node["equipment"] = _apply_equipment_changes(
            node.get("equipment", []),
            equipment_additions,
            equipment_removals,
        )
        required_equipment.update(node["equipment"])

    if "equipment" not in adapted:
        adapted["equipment"] = _apply_equipment_changes(
            [], equipment_additions, equipment_removals
        )
        required_equipment.update(adapted["equipment"])

    for equipment in required_equipment:
        if _key(equipment) not in available:
            reasons.add("equipment {} unavailable".format(equipment))

    original_yield = Fraction(recipe["yield"])
    effective_yield = (
        yield_override
        if yield_override is not None
        else original_yield * yield_factor
    )
    target_yield = Fraction(request["target_yield"])

    if effective_yield == 0:
        reasons.add(
            "substitution cycle involving {}".format(recipe.get("title", ""))
        )
        scale = Fraction(1)
    else:
        scale = target_yield / effective_yield

    for node in nodes:
        ingredients = node.get("ingredients")
        if not isinstance(ingredients, list):
            continue
        for ingredient in ingredients:
            ingredient["quantity"] = (
                Fraction(ingredient.get("quantity", 0)) * scale
            )

    adapted["yield"] = target_yield
    ordered_reasons = sorted(reasons)

    return {
        "possible": not ordered_reasons,
        "recipe": adapted if not ordered_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": ordered_reasons,
    }


def print_original(recipe):
    """Return the authored recipe text byte-for-byte as a Python string."""
    return recipe["authored_text"]
