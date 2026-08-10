"""recipebook.py -- adapt recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog.

Public API: adapt(recipe, request, catalog) -> dict
            print_original(recipe) -> str
Quantities and yields are fractions.Fraction; only the stdlib is used.
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as authored, byte for byte."""
    return recipe["authored_text"]


def _options_for(catalog, name):
    """Catalog choices for NAME, best first: lower priority wins, catalog
    order breaks ties."""
    found = [(choice.get("priority", 0), index, choice)
             for index, choice in enumerate(catalog)
             if choice.get("for") == name]
    found.sort(key=lambda entry: (entry[0], entry[1]))
    return [choice for _, _, choice in found]


def adapt(recipe, request, catalog):
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])
    excluded = set(request.get("excluded") or ())
    available = set(request.get("available_equipment") or ())

    reasons = set()
    warnings = []
    chosen = []
    wording_changes = []
    equipment = set(recipe.get("equipment") or ())
    yield_factor = Fraction(1)

    def resolve(ing, chain):
        # Returns the list of adapted ingredients this one expands to, or
        # None when its exclusion cannot be satisfied (reason recorded).
        # Processing continues past failures so every reason is collected.
        nonlocal yield_factor
        name = ing["name"]
        if name not in excluded:
            return [ing]
        if name in chain:
            reasons.add("substitution cycle involving " + name)
            return None
        options = _options_for(catalog, name)
        if not options:
            reasons.add("no substitution for " + name)
            return None
        choice = options[0]
        chosen.append(choice["id"])
        result = choice["result"]
        replacement = {
            "id": ing.get("id"),
            "name": result["name"],
            "quantity": ing["quantity"] * Fraction(result.get("quantity_factor", 1)),
            "unit": result.get("unit", ing.get("unit")),
            "preparation": result.get("preparation", ing.get("preparation")),
        }
        wording_changes.extend(result.get("wording_changes") or ())
        for item in result.get("equipment_additions") or ():
            equipment.add(item)
        for item in result.get("equipment_removals") or ():
            equipment.discard(item)
        if "yield_factor" in result:
            yield_factor *= Fraction(result["yield_factor"])
            warnings.append("yield changed by %s; quantities rescaled to target"
                            % choice["id"])
        chain = chain | {name}
        if replacement["name"] in excluded:
            warnings.append("excluded ingredient %s reintroduced by %s"
                            % (replacement["name"], choice["id"]))
        produced = []
        failed = False
        expansion = resolve(replacement, chain)
        if expansion is None:
            failed = True
        else:
            produced.extend(expansion)
        for extra in result.get("additional_ingredients") or ():
            extra_ing = {
                "id": extra.get("id", extra["name"]),
                "name": extra["name"],
                "quantity": Fraction(extra["quantity"]) * scale,
                "unit": extra.get("unit"),
                "preparation": extra.get("preparation", ""),
            }
            expansion = resolve(extra_ing, chain)
            if expansion is None:
                failed = True
            else:
                produced.extend(expansion)
        return None if failed else produced

    def adapt_ingredients(ingredients):
        adapted = []
        for ing in ingredients:
            scaled = {
                "id": ing.get("id"),
                "name": ing["name"],
                "quantity": Fraction(ing["quantity"]) * scale,
                "unit": ing.get("unit"),
                "preparation": ing.get("preparation", ""),
            }
            expansion = resolve(scaled, frozenset())
            if expansion is not None:
                adapted.extend(expansion)
        return adapted

    main_ingredients = adapt_ingredients(recipe.get("ingredients") or ())

    components = []
    for comp in recipe.get("components") or ():
        new_comp = {key: copy.deepcopy(value) for key, value in comp.items()
                    if key not in ("ingredients", "instructions")}
        if "ingredients" in comp:
            new_comp["ingredients"] = adapt_ingredients(comp["ingredients"])
        if "instructions" in comp:
            new_comp["instructions"] = list(comp["instructions"])
        components.append(new_comp)

    for item in sorted(equipment):
        if item not in available:
            reasons.add("equipment %s unavailable" % item)

    if reasons:
        return {"possible": False, "recipe": None, "choices": chosen,
                "warnings": sorted(warnings), "reasons": sorted(reasons)}

    def reword(text):
        for change in wording_changes:
            text = text.replace(change["old"], change["new"])
        return text

    if yield_factor != 1:
        for ing in main_ingredients:
            ing["quantity"] /= yield_factor
        for comp in components:
            for ing in comp.get("ingredients") or ():
                ing["quantity"] /= yield_factor

    for comp in components:
        if "instructions" in comp:
            comp["instructions"] = [reword(text) for text in comp["instructions"]]

    adapted = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": main_ingredients,
        "equipment": sorted(equipment),
        "instructions": [reword(text) for text in recipe.get("instructions") or ()],
        "components": components,
        "authored_text": recipe.get("authored_text"),
    }
    return {"possible": True, "recipe": adapted, "choices": chosen,
            "warnings": sorted(warnings), "reasons": []}
