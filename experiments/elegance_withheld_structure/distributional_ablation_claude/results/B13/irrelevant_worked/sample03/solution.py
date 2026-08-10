"""recipebook: adapt authored recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.

A recipe is {title, yield, ingredients, equipment, instructions, components,
authored_text}; each ingredient is {id, name, quantity, unit, preparation}.
Each component is {name, optional?, ingredients, instructions?}; an optional
component that cannot be adapted is omitted with a warning instead of making
the whole adaptation impossible.

Catalog choices are {id, for, priority, result}. Keys understood inside
``result`` (all optional except ``name``):
    name                    replacement ingredient name
    quantity_factor         multiplies the ingredient quantity
    unit                    replaces the unit
    preparation             replaces the preparation wording
    wording_changes         list of {"old": .., "new": ..} instruction edits
    add_equipment           equipment names the substitute requires
    remove_equipment        equipment names no longer required
    additional_ingredients  ingredient-shaped dicts introduced alongside
    yield_factor            the substitute changes the effective yield; all
                            quantities are rescaled so the stated yield stays
                            at the requested target

Lower numeric priority wins; catalog order breaks ties. Neither public
function mutates its arguments. Standard library only.
"""

from fractions import Fraction


def print_original(recipe):
    """Return the authored recipe text byte-for-byte."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    """Adapt ``recipe`` to ``request`` using ``catalog`` substitutions.

    Returns {possible, recipe, choices, warnings, reasons}.
    """
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])

    ctx = _new_context()
    ingredients = _adapt_ingredients(
        recipe["ingredients"], scale, catalog, excluded, ctx)

    components = []
    for component in recipe.get("components") or []:
        local = _new_context()
        adapted_items = _adapt_ingredients(
            component.get("ingredients") or [], scale, catalog, excluded, local)
        if local["reasons"] and component.get("optional"):
            ctx["warnings"].append(
                "omitted optional component " + component.get("name", ""))
            continue
        _merge_context(ctx, local)
        adapted_component = dict(component)
        adapted_component["ingredients"] = adapted_items
        components.append(adapted_component)

    equipment = (set(recipe.get("equipment") or [])
                 | ctx["needed_equipment"]) - ctx["dropped_equipment"]
    for item in equipment - available:
        ctx["reasons"].append("equipment " + item + " unavailable")

    warnings = sorted(ctx["warnings"])
    reasons = sorted(set(ctx["reasons"]))
    if reasons:
        return {"possible": False, "recipe": None,
                "choices": ctx["choices"], "warnings": warnings,
                "reasons": reasons}

    if ctx["yield_factor"] != 1:
        rescale = Fraction(1) / ctx["yield_factor"]
        for ingredient in ingredients:
            ingredient["quantity"] *= rescale
        for component in components:
            for ingredient in component["ingredients"]:
                ingredient["quantity"] *= rescale

    for component in components:
        if "instructions" in component:
            component["instructions"] = [
                _reword(text, ctx["rewordings"])
                for text in component["instructions"]]

    adapted = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": ingredients,
        "equipment": sorted(equipment),
        "instructions": [_reword(text, ctx["rewordings"])
                         for text in recipe["instructions"]],
        "components": components,
        "authored_text": recipe["authored_text"],
    }
    return {"possible": True, "recipe": adapted,
            "choices": ctx["choices"], "warnings": warnings, "reasons": []}


def _new_context():
    return {
        "choices": [],
        "warnings": [],
        "reasons": [],
        "rewordings": [],
        "needed_equipment": set(),
        "dropped_equipment": set(),
        "extras": [],
        "yield_factor": Fraction(1),
    }


def _merge_context(ctx, local):
    ctx["choices"].extend(local["choices"])
    ctx["warnings"].extend(local["warnings"])
    ctx["reasons"].extend(local["reasons"])
    ctx["rewordings"].extend(local["rewordings"])
    ctx["needed_equipment"] |= local["needed_equipment"]
    ctx["dropped_equipment"] |= local["dropped_equipment"]
    ctx["yield_factor"] *= local["yield_factor"]


def _ranked_choices(catalog, name):
    matches = [(choice.get("priority", 0), index, choice)
               for index, choice in enumerate(catalog)
               if choice["for"] == name]
    matches.sort(key=lambda entry: (entry[0], entry[1]))
    return [choice for _, _, choice in matches]


def _adapt_ingredients(items, scale, catalog, excluded, ctx):
    adapted = []
    pending = list(items)
    while pending:
        raw = pending.pop(0)
        ingredient = {
            "id": raw.get("id", raw["name"]),
            "name": raw["name"],
            "quantity": Fraction(raw.get("quantity", 1)) * scale,
            "unit": raw.get("unit"),
            "preparation": raw.get("preparation", ""),
        }
        resolved = _resolve(ingredient, catalog, excluded, ctx)
        if resolved is not None:
            adapted.append(resolved)
        # Ingredients introduced by a substitution face the same exclusions
        # and catalog choices as authored ones.
        pending.extend(ctx["extras"])
        ctx["extras"] = []
    return adapted


def _resolve(ingredient, catalog, excluded, ctx):
    seen = {ingredient["name"]}
    while ingredient["name"] in excluded:
        ranked = _ranked_choices(catalog, ingredient["name"])
        if not ranked:
            ctx["reasons"].append("no substitution for " + ingredient["name"])
            return None
        choice = ranked[0]
        ctx["choices"].append(choice["id"])
        result = choice["result"]
        former = ingredient["name"]
        ingredient["name"] = result["name"]
        if "quantity_factor" in result:
            ingredient["quantity"] *= Fraction(result["quantity_factor"])
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]
        for change in result.get("wording_changes") or []:
            ctx["rewordings"].append((change["old"], change["new"]))
        ctx["needed_equipment"].update(result.get("add_equipment") or [])
        ctx["dropped_equipment"].update(result.get("remove_equipment") or [])
        if "yield_factor" in result:
            ctx["yield_factor"] *= Fraction(result["yield_factor"])
        ctx["extras"].extend(result.get("additional_ingredients") or [])
        ctx["warnings"].append(
            "substituted " + former + " with " + ingredient["name"])
        if ingredient["name"] in excluded and ingredient["name"] in seen:
            ctx["reasons"].append(
                "substitution cycle involving " + ingredient["name"])
            return None
        seen.add(ingredient["name"])
    return ingredient


def _reword(text, rewordings):
    for old, new in rewordings:
        text = text.replace(old, new)
    return text
