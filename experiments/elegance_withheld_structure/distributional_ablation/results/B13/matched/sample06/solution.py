from copy import deepcopy
from fractions import Fraction


def _key(value):
    return str(value).strip().casefold()


def _fraction(value):
    return value if isinstance(value, Fraction) else Fraction(value)


def _sections(recipe):
    """Yield the recipe and every recipe-shaped component."""
    seen = set()

    def visit(value):
        if isinstance(value, dict):
            marker = id(value)
            if marker in seen:
                return
            seen.add(marker)

            if any(
                field in value
                for field in ("ingredients", "equipment", "instructions", "components")
            ):
                yield value

            components = value.get("components", [])
            if isinstance(components, dict):
                components = components.values()
            if isinstance(components, (list, tuple)) or hasattr(components, "__iter__"):
                for component in components:
                    if isinstance(component, dict):
                        yield from visit(component)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from visit(item)

    yield from visit(recipe)


def _ingredients(recipe):
    for section in _sections(recipe):
        values = section.get("ingredients", [])
        if isinstance(values, list):
            for ingredient in values:
                if isinstance(ingredient, dict):
                    yield ingredient


def _catalog_entries(catalog):
    if isinstance(catalog, dict):
        catalog = catalog.get("choices", [])
    if catalog is None:
        catalog = []
    indexed = list(enumerate(catalog))
    indexed.sort(key=lambda item: (item[1].get("priority", 0), item[0]))
    return [choice for _, choice in indexed]


def _choice_matches(choice, ingredient):
    target = choice.get("for")
    targets = target if isinstance(target, (list, tuple, set)) else [target]
    identities = {_key(ingredient.get("name", "")), _key(ingredient.get("id", ""))}
    return any(_key(candidate) in identities for candidate in targets)


def _equipment_changes(result):
    additions = result.get("equipment_additions", result.get("add_equipment", []))
    removals = result.get("equipment_removals", result.get("remove_equipment", []))

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions = list(additions or []) + list(
            equipment.get("additions", equipment.get("add", [])) or []
        )
        removals = list(removals or []) + list(
            equipment.get("removals", equipment.get("remove", [])) or []
        )

    return list(additions or []), list(removals or [])


def _apply_wording(recipe, changes):
    for change in changes or []:
        if isinstance(change, dict):
            old = change.get("old")
            new = change.get("new")
        elif isinstance(change, (list, tuple)) and len(change) == 2:
            old, new = change
        else:
            continue

        if not isinstance(old, str) or not isinstance(new, str) or not old:
            continue

        for section in _sections(recipe):
            instructions = section.get("instructions")
            if isinstance(instructions, list):
                section["instructions"] = [
                    text.replace(old, new) if isinstance(text, str) else text
                    for text in instructions
                ]


def _apply_equipment(recipe, additions, removals):
    removed = {_key(item) for item in removals}

    for section in _sections(recipe):
        equipment = section.get("equipment")
        if isinstance(equipment, list):
            section["equipment"] = [
                item for item in equipment if _key(item) not in removed
            ]

    root_equipment = recipe.setdefault("equipment", [])
    existing = {_key(item) for item in root_equipment}
    for item in additions:
        if _key(item) not in existing:
            root_equipment.append(item)
            existing.add(_key(item))


def _multiply_all_quantities(recipe, factor):
    factor = _fraction(factor)
    for ingredient in _ingredients(recipe):
        if "quantity" in ingredient:
            ingredient["quantity"] = _fraction(ingredient["quantity"]) * factor


def _scale_recipe(recipe, factor, target_yield):
    factor = _fraction(factor)
    sections = list(_sections(recipe))
    for section in sections:
        for ingredient in section.get("ingredients", []):
            if isinstance(ingredient, dict) and "quantity" in ingredient:
                ingredient["quantity"] = _fraction(ingredient["quantity"]) * factor

        if section is not recipe and isinstance(section.get("yield"), Fraction):
            section["yield"] = section["yield"] * factor

    recipe["yield"] = target_yield


def _normalize_equipment(recipe, available, reasons):
    available_by_key = {}
    for item in available:
        available_by_key.setdefault(_key(item), item)

    for section in _sections(recipe):
        equipment = section.get("equipment")
        if not isinstance(equipment, list):
            continue

        normalized = []
        seen = set()
        for item in equipment:
            key = _key(item)
            if key not in available_by_key:
                reasons.add(f"equipment {item} unavailable")
                output = item
            else:
                output = available_by_key[key]

            if _key(output) not in seen:
                normalized.append(output)
                seen.add(_key(output))

        section["equipment"] = sorted(normalized)


def adapt(recipe, request, catalog):
    """Return a complete adapted recipe or all independently applicable failures."""
    adapted = deepcopy(recipe)
    choices = []
    warnings = set()
    reasons = set()

    target_yield = _fraction(request["target_yield"])
    original_yield = _fraction(adapted["yield"])
    scale = target_yield / original_yield
    _scale_recipe(adapted, scale, target_yield)

    excluded = {_key(item) for item in request.get("excluded", [])}
    ordered_catalog = _catalog_entries(catalog)
    state = {"addition_scale": scale}

    def find_choice(ingredient):
        for choice in ordered_catalog:
            if _choice_matches(choice, ingredient):
                return choice
        return None

    def process_ingredient(ingredient, container, ancestry=()):
        while _key(ingredient.get("name", "")) in excluded:
            name = ingredient.get("name", "")
            name_key = _key(name)

            if name_key in ancestry:
                reasons.add(f"substitution cycle involving {name}")
                return

            choice = find_choice(ingredient)
            if choice is None:
                reasons.add(f"no substitution for {name}")
                return

            choices.append(choice.get("id"))
            result = choice.get("result") or {}
            next_ancestry = ancestry + (name_key,)

            if "quantity_factor" in result and "quantity" in ingredient:
                ingredient["quantity"] = (
                    _fraction(ingredient["quantity"])
                    * _fraction(result["quantity_factor"])
                )

            ingredient["name"] = result.get("name", ingredient.get("name"))
            if "unit" in result:
                ingredient["unit"] = result["unit"]
            if "preparation" in result:
                ingredient["preparation"] = result["preparation"]

            _apply_wording(adapted, result.get("wording_changes", []))
            additions, removals = _equipment_changes(result)
            _apply_equipment(adapted, additions, removals)

            yield_factor = result.get("yield_factor")
            absolute_yield = result.get("yield", result.get("result_yield"))
            compensation = Fraction(1)
            if yield_factor is not None:
                factor = _fraction(yield_factor)
                if factor:
                    compensation = Fraction(1, 1) / factor
            elif absolute_yield is not None:
                produced = _fraction(absolute_yield)
                if produced:
                    compensation = target_yield / produced

            if compensation != 1:
                _multiply_all_quantities(adapted, compensation)
                state["addition_scale"] *= compensation

            added = result.get("additional_ingredients", []) or []
            for supplied in added:
                if not isinstance(supplied, dict):
                    continue
                extra = deepcopy(supplied)
                if "quantity" in extra:
                    extra["quantity"] = (
                        _fraction(extra["quantity"]) * state["addition_scale"]
                    )
                container.append(extra)
                process_ingredient(extra, container, next_ancestry)

            ancestry = next_ancestry

    # Snapshot each original list so recursively appended ingredients are not
    # processed twice; process_ingredient handles every addition immediately.
    for section in list(_sections(adapted)):
        container = section.get("ingredients")
        if isinstance(container, list):
            for ingredient in list(container):
                if isinstance(ingredient, dict):
                    process_ingredient(ingredient, container)

    available = list(request.get("available_equipment", []))
    _normalize_equipment(adapted, available, reasons)

    # Defensive final check for any excluded ingredient a malformed catalog left.
    for ingredient in _ingredients(adapted):
        if _key(ingredient.get("name", "")) in excluded:
            name = ingredient.get("name", "")
            cycle_reason = f"substitution cycle involving {name}"
            missing_reason = f"no substitution for {name}"
            if cycle_reason not in reasons and missing_reason not in reasons:
                reasons.add(missing_reason)

    adapted["yield"] = target_yield
    sorted_reasons = sorted(reasons)
    return {
        "possible": not sorted_reasons,
        "recipe": adapted if not sorted_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted_reasons,
    }


def print_original(recipe):
    """Return the original authored representation byte-for-byte."""
    return recipe["authored_text"]
