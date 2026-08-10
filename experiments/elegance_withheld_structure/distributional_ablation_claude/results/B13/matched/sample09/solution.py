"""recipebook: adapt authored recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog, without touching the original.

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Only the standard library is used.
"""

from fractions import Fraction

__all__ = ["adapt", "print_original"]


def print_original(recipe):
    """Return the recipe exactly as authored, byte-for-byte."""
    return recipe["authored_text"]


def _fresh_ingredient(raw):
    """Build an independent ingredient record so inputs are never mutated."""
    return {
        "id": raw.get("id", raw["name"]),
        "name": raw["name"],
        "quantity": Fraction(raw.get("quantity", 1)),
        "unit": raw.get("unit", ""),
        "preparation": raw.get("preparation", ""),
    }


def _best_choice(name, catalog):
    """Lowest numeric priority wins; catalog order breaks ties."""
    best = None
    best_key = None
    for index, choice in enumerate(catalog):
        if choice["for"] != name:
            continue
        key = (choice["priority"], index)
        if best_key is None or key < best_key:
            best, best_key = choice, key
    return best


class _State:
    """Working state shared across the whole adaptation pass."""

    def __init__(self, scale, equipment):
        self.scale = scale
        self.equipment = equipment      # working copy of the equipment set
        self.choices = []               # applied catalog IDs, in order
        self.warnings = set()
        self.reasons = set()
        self.wording = []               # ordered (old, new) instruction edits
        self.pending = []               # substitution-added ingredients
        self.yield_factor = Fraction(1)


def _apply_result(ing, result, state):
    """Apply one catalog result, keeping every dependent field consistent."""
    ing["name"] = result["name"]
    if "quantity_factor" in result:
        ing["quantity"] = ing["quantity"] * Fraction(result["quantity_factor"])
    if "unit" in result and result["unit"] != ing["unit"]:
        state.warnings.add(
            "unit of %s changed from %s to %s"
            % (result["name"], ing["unit"], result["unit"])
        )
        ing["unit"] = result["unit"]
    if "preparation" in result:
        ing["preparation"] = result["preparation"]
    for pair in result.get("wording_changes", []):
        state.wording.append((pair["old"], pair["new"]))
    for item in result.get("equipment_additions", []):
        state.equipment.add(item)
    for item in result.get("equipment_removals", []):
        state.equipment.discard(item)
    if "yield_factor" in result:
        state.yield_factor *= Fraction(result["yield_factor"])
        state.warnings.add(
            "substitution %s alters yield; quantities rescaled to target"
            % result["name"]
        )
    for extra in result.get("additional_ingredients", []):
        added = _fresh_ingredient(extra)
        added["quantity"] = added["quantity"] * state.scale
        state.pending.append(added)


def _resolve(ing, excluded, catalog, state):
    """Chain substitutions until the ingredient is acceptable.

    Returns True when the ingredient survives, False when a failure reason
    (missing substitution or cycle) was recorded.
    """
    seen = {ing["name"]}
    while ing["name"] in excluded:
        choice = _best_choice(ing["name"], catalog)
        if choice is None:
            state.reasons.add("no substitution for %s" % ing["name"])
            return False
        state.choices.append(choice["id"])
        _apply_result(ing, choice["result"], state)
        name = ing["name"]
        if name in excluded:
            if name in seen:
                state.reasons.add("substitution cycle involving %s" % name)
                return False
            state.warnings.add(
                "substitution reintroduced excluded ingredient %s" % name
            )
        seen.add(name)
    return True


def adapt(recipe, request, catalog):
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])

    state = _State(scale, set(recipe["equipment"]))

    def adapt_ingredients(raw_list):
        kept = []
        for raw in raw_list:
            ing = _fresh_ingredient(raw)
            ing["quantity"] = ing["quantity"] * scale
            if _resolve(ing, excluded, catalog, state):
                kept.append(ing)
        return kept

    ingredients = adapt_ingredients(recipe["ingredients"])

    components = []
    for comp in recipe.get("components", []) or []:
        new_comp = dict(comp)
        if "ingredients" in comp:
            new_comp["ingredients"] = adapt_ingredients(comp["ingredients"])
        if "instructions" in comp:
            new_comp["instructions"] = list(comp["instructions"])
        if "equipment" in comp:
            new_comp["equipment"] = sorted(comp["equipment"])
            for item in new_comp["equipment"]:
                if item not in available:
                    state.reasons.add("equipment %s unavailable" % item)
        components.append(new_comp)

    # Ingredients introduced by substitutions face the same exclusions and
    # catalog choices, and may themselves introduce further ingredients.
    while state.pending:
        ing = state.pending.pop(0)
        if _resolve(ing, excluded, catalog, state):
            ingredients.append(ing)

    for item in sorted(state.equipment):
        if item not in available:
            state.reasons.add("equipment %s unavailable" % item)

    warnings = sorted(state.warnings)
    if state.reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": state.choices,
            "warnings": warnings,
            "reasons": sorted(state.reasons),
        }

    # A substitution that altered yield is compensated so the adapted recipe
    # is exactly at the requested target yield.
    if state.yield_factor != 1:
        for ing in ingredients:
            ing["quantity"] = ing["quantity"] / state.yield_factor
        for comp in components:
            for ing in comp.get("ingredients", []):
                ing["quantity"] = ing["quantity"] / state.yield_factor

    def rewrite(text):
        for old, new in state.wording:
            text = text.replace(old, new)
        return text

    instructions = [rewrite(line) for line in recipe["instructions"]]
    for comp in components:
        if "instructions" in comp:
            comp["instructions"] = [rewrite(line) for line in comp["instructions"]]

    adapted = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": ingredients,
        "equipment": sorted(state.equipment),
        "instructions": instructions,
        "components": components,
        "authored_text": recipe["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": state.choices,
        "warnings": warnings,
        "reasons": [],
    }
