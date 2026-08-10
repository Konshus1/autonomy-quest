"""recipebook: adapt authored recipes for yield, equipment, and diet.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; other values are JSON-shaped.
A catalog choice is {id, for, priority, result}.  ``result`` holds the
replacement ingredient fields (``name`` plus optional ``quantity_factor``,
``unit``, ``preparation``) and may also hold:
    wording_changes          list of {old, new} instruction edits
    add_equipment            list of equipment names the substitute needs
    remove_equipment         list of equipment names no longer needed
    additional_ingredients   list of ingredient dicts (same shape as recipe
                             ingredients; scaled and substituted like any other)
    yield_factor             Fraction-compatible multiplier on the final yield
Lower numeric priority wins; catalog order breaks ties.
Neither public function mutates its arguments.
"""

from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as authored."""
    return recipe["authored_text"]


def _best_choice(catalog, name):
    """Choice with lowest (priority, catalog index) for ``name``, or None."""
    best_key = None
    best = None
    for index, choice in enumerate(catalog):
        if choice["for"] != name:
            continue
        key = (choice["priority"], index)
        if best_key is None or key < best_key:
            best_key, best = key, choice
    return best


class _Effects:
    """Accumulated observable effects of adapting a group of ingredients."""

    def __init__(self):
        self.choices = []
        self.warnings = set()
        self.reasons = set()
        self.wording = []
        self.equipment_add = set()
        self.equipment_remove = set()
        self.yield_factor = Fraction(1)

    def merge(self, other):
        self.choices.extend(other.choices)
        self.warnings |= other.warnings
        self.reasons |= other.reasons
        self.wording.extend(other.wording)
        self.equipment_add |= other.equipment_add
        self.equipment_remove |= other.equipment_remove
        self.yield_factor *= other.yield_factor


def _resolve_ingredient(ing, scale, excluded, catalog, effects, seen):
    """Scale one ingredient and substitute until no excluded name remains.

    Returns a list of final ingredients (the resolved ingredient plus any
    additional ingredients its substitutions introduced), or None on failure
    after recording the reason(s) in ``effects``.
    """
    current = {
        "id": ing.get("id"),
        "name": ing["name"],
        "quantity": ing["quantity"] * scale,
        "unit": ing.get("unit"),
        "preparation": ing.get("preparation"),
    }
    seen = set(seen)
    pending_extras = []
    while current["name"] in excluded:
        seen.add(current["name"])
        choice = _best_choice(catalog, current["name"])
        if choice is None:
            effects.reasons.add("no substitution for " + current["name"])
            return None
        effects.choices.append(choice["id"])
        result = choice["result"]
        new_name = result["name"]
        if new_name in seen:
            effects.reasons.add("substitution cycle involving " + new_name)
            return None
        if new_name in excluded:
            effects.warnings.add(
                "choice %s reintroduced excluded ingredient %s"
                % (choice["id"], new_name))
        current["name"] = new_name
        current["quantity"] = current["quantity"] * Fraction(
            result.get("quantity_factor", 1))
        if "unit" in result:
            current["unit"] = result["unit"]
        if "preparation" in result:
            current["preparation"] = result["preparation"]
        for pair in result.get("wording_changes", ()):
            effects.wording.append((pair["old"], pair["new"]))
        effects.equipment_add.update(result.get("add_equipment", ()))
        effects.equipment_remove.update(result.get("remove_equipment", ()))
        factor = result.get("yield_factor")
        if factor is not None and Fraction(factor) != 1:
            effects.yield_factor *= Fraction(factor)
            effects.warnings.add(
                "choice %s changed yield by factor %s"
                % (choice["id"], Fraction(factor)))
        pending_extras.extend(
            (extra, set(seen))
            for extra in result.get("additional_ingredients", ()))
    resolved = [current]
    failed = False
    for extra, extra_seen in pending_extras:
        sub = _resolve_ingredient(
            extra, scale, excluded, catalog, effects, extra_seen)
        if sub is None:
            failed = True
        else:
            resolved.extend(sub)
    return None if failed else resolved


def _resolve_all(ingredients, scale, excluded, catalog, effects):
    out = []
    ok = True
    for ing in ingredients:
        resolved = _resolve_ingredient(
            ing, scale, excluded, catalog, effects, set())
        if resolved is None:
            ok = False
        else:
            out.extend(resolved)
    return out, ok


def _apply_wording(text, wording):
    for old, new in wording:
        text = text.replace(old, new)
    return text


def adapt(recipe, request, catalog):
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / Fraction(recipe["yield"])

    effects = _Effects()
    ingredients, _ = _resolve_all(
        recipe["ingredients"], scale, excluded, catalog, effects)

    components = []
    for comp in recipe.get("components") or ():
        comp_effects = _Effects()
        comp_ings, comp_ok = _resolve_all(
            comp.get("ingredients") or (), scale, excluded, catalog,
            comp_effects)
        comp_equipment = sorted(comp.get("equipment") or ())
        for item in comp_equipment:
            if item not in available:
                comp_effects.reasons.add("equipment %s unavailable" % item)
                comp_ok = False
        if comp_ok:
            effects.merge(comp_effects)
            adapted = dict(comp)
            adapted["ingredients"] = comp_ings
            if "equipment" in comp:
                adapted["equipment"] = comp_equipment
            if "instructions" in comp:
                adapted["instructions"] = list(comp["instructions"])
            components.append(adapted)
        elif comp.get("optional"):
            effects.choices.extend(comp_effects.choices)
            effects.warnings.add(
                "dropped optional component %s"
                % comp.get("name", "component"))
        else:
            effects.merge(comp_effects)

    equipment = sorted(
        (set(recipe.get("equipment") or ()) | effects.equipment_add)
        - effects.equipment_remove)
    for item in equipment:
        if item not in available:
            effects.reasons.add("equipment %s unavailable" % item)

    if effects.reasons:
        adapted_recipe = None
    else:
        instructions = [
            _apply_wording(text, effects.wording)
            for text in recipe["instructions"]]
        for comp in components:
            if "instructions" in comp:
                comp["instructions"] = [
                    _apply_wording(text, effects.wording)
                    for text in comp["instructions"]]
        adapted_recipe = {
            "title": recipe["title"],
            "yield": target_yield * effects.yield_factor,
            "ingredients": ingredients,
            "equipment": equipment,
            "instructions": instructions,
            "components": components,
            "authored_text": recipe["authored_text"],
        }

    return {
        "possible": adapted_recipe is not None,
        "recipe": adapted_recipe,
        "choices": list(effects.choices),
        "warnings": sorted(effects.warnings),
        "reasons": sorted(effects.reasons),
    }
