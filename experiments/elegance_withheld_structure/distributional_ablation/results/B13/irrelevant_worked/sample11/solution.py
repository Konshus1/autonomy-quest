"""Recipe adaptation using exact rational arithmetic."""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe's authored text byte-for-byte."""
    return recipe["authored_text"]


def _key(value):
    return str(value).casefold()


def _catalog_choices(catalog):
    if isinstance(catalog, dict):
        catalog = catalog.get("choices", [])
    return list(catalog or [])


def _recipe_sections(value):
    """Yield the root recipe and any recipe-shaped components."""
    if not isinstance(value, dict):
        return

    if isinstance(value.get("ingredients"), list):
        yield value

    components = value.get("components")
    if isinstance(components, list):
        for component in components:
            yield from _recipe_sections(component)
    elif isinstance(components, dict):
        if isinstance(components.get("ingredients"), list):
            yield from _recipe_sections(components)
        else:
            for component in components.values():
                yield from _recipe_sections(component)


def _change_wording(recipe, old, new):
    for section in _recipe_sections(recipe):
        instructions = section.get("instructions")
        if isinstance(instructions, list):
            section["instructions"] = [
                text.replace(old, new) if isinstance(text, str) else text
                for text in instructions
            ]


def _equipment_effect(result, operation):
    aliases = {
        "add": (
            "equipment_additions",
            "add_equipment",
            "equipment_add",
        ),
        "remove": (
            "equipment_removals",
            "remove_equipment",
            "equipment_remove",
        ),
    }

    for field in aliases[operation]:
        if field in result:
            value = result[field]
            if value is None:
                return []
            if isinstance(value, str):
                return [value]
            return list(value)

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        keys = {
            "add": ("additions", "add"),
            "remove": ("removals", "remove"),
        }
        for field in keys[operation]:
            if field in equipment:
                value = equipment[field]
                if value is None:
                    return []
                if isinstance(value, str):
                    return [value]
                return list(value)

    return []


def adapt(recipe, request, catalog):
    """Return an isolated recipe adaptation satisfying the public contract."""
    adapted = deepcopy(recipe)
    request = deepcopy(request)
    catalog = deepcopy(_catalog_choices(catalog))

    excluded = {_key(name) for name in request.get("excluded", [])}
    available = set(request.get("available_equipment", []))
    target_yield = Fraction(request["target_yield"])

    indexed = {}
    for position, choice in enumerate(catalog):
        subject = choice.get("for")
        subjects = subject if isinstance(subject, (list, tuple, set)) else [subject]
        priority = Fraction(choice.get("priority", 0))
        for item in subjects:
            if item is not None:
                indexed.setdefault(_key(item), []).append(
                    (priority, position, choice)
                )

    for candidates in indexed.values():
        candidates.sort(key=lambda item: (item[0], item[1]))

    chosen_ids = []
    warnings = set()
    reasons = set()
    equipment = list(adapted.get("equipment", []))
    effective_yield = Fraction(adapted["yield"])

    def choose(ingredient):
        candidates = []
        seen_positions = set()

        for lookup in (ingredient.get("name"), ingredient.get("id")):
            if lookup is None:
                continue
            for candidate in indexed.get(_key(lookup), []):
                if candidate[1] not in seen_positions:
                    seen_positions.add(candidate[1])
                    candidates.append(candidate)

        if not candidates:
            return None

        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    def apply_effects(result):
        nonlocal equipment, effective_yield

        removals = set(_equipment_effect(result, "remove"))
        equipment = [item for item in equipment if item not in removals]

        for item in _equipment_effect(result, "add"):
            if item not in equipment:
                equipment.append(item)

        for change in result.get("wording_changes", []) or []:
            if isinstance(change, dict):
                old = change.get("old")
                new = change.get("new")
            else:
                old, new = change
            if isinstance(old, str) and isinstance(new, str):
                _change_wording(adapted, old, new)

        if "yield" in result:
            effective_yield = Fraction(result["yield"])
        if "yield_factor" in result:
            effective_yield *= Fraction(result["yield_factor"])

    def resolve(ingredient, lineage):
        current = deepcopy(ingredient)
        name = str(current.get("name", ""))
        name_key = _key(name)

        if name_key not in excluded:
            return [current]

        if name_key in lineage:
            reasons.add("substitution cycle involving " + name)
            return []

        choice = choose(current)
        if choice is None:
            reasons.add("no substitution for " + name)
            return []

        chosen_ids.append(choice["id"])
        result = choice.get("result", {}) or {}
        apply_effects(result)

        replacement = deepcopy(current)
        replacement["name"] = result.get("name", replacement.get("name"))

        if "quantity_factor" in result:
            replacement["quantity"] = (
                Fraction(replacement["quantity"])
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        next_lineage = lineage + (name_key,)
        resolved = resolve(replacement, next_lineage)

        for addition in result.get("additional_ingredients", []) or []:
            resolved.extend(resolve(addition, next_lineage))

        return resolved

    for section in list(_recipe_sections(adapted)):
        resolved = []
        for ingredient in section.get("ingredients", []):
            resolved.extend(resolve(ingredient, ()))
        section["ingredients"] = resolved

    adapted["equipment"] = sorted(set(equipment))
    for item in adapted["equipment"]:
        if item not in available:
            reasons.add("equipment " + str(item) + " unavailable")

    for section in _recipe_sections(adapted):
        if section is adapted:
            continue
        component_equipment = section.get("equipment")
        if isinstance(component_equipment, list):
            section["equipment"] = sorted(set(component_equipment))
            for item in section["equipment"]:
                if item not in available:
                    reasons.add("equipment " + str(item) + " unavailable")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": chosen_ids,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    scale = target_yield / effective_yield
    for section in _recipe_sections(adapted):
        for ingredient in section.get("ingredients", []):
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    adapted["yield"] = target_yield

    return {
        "possible": True,
        "recipe": adapted,
        "choices": chosen_ids,
        "warnings": sorted(warnings),
        "reasons": [],
    }
