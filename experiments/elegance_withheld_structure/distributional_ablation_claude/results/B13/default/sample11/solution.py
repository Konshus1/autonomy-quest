"""recipebook: adapt recipes to a target yield, available equipment, and
dietary exclusions using a substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction. Catalog ``result`` keys honored:
name, quantity_factor, unit, preparation, yield_factor, wording_changes
(list of {old, new}), add_equipment, remove_equipment, additional_ingredients.
Neither function mutates its inputs.
"""

from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as authored."""
    return recipe["authored_text"]


def _sorted_candidates(catalog, name):
    """Catalog choices for *name*, lowest priority first, catalog order as tie-break."""
    matches = [(choice.get("priority", 0), index, choice)
               for index, choice in enumerate(catalog)
               if choice.get("for") == name]
    matches.sort(key=lambda item: (item[0], item[1]))
    return [choice for _, _, choice in matches]


class _Ctx:
    """Accumulated adaptation state."""

    def __init__(self):
        self.choices = []          # ordered chosen catalog IDs
        self.warnings = set()
        self.reasons = set()
        self.wording = []          # ordered {old, new} pairs
        self.equip_add = set()
        self.equip_remove = set()
        self.yield_corr = Fraction(1)

    def merge(self, other):
        self.choices.extend(other.choices)
        self.warnings |= other.warnings
        self.reasons |= other.reasons
        self.wording.extend(other.wording)
        self.equip_add |= other.equip_add
        self.equip_remove |= other.equip_remove
        self.yield_corr *= other.yield_corr


def _adapt_ingredient(ingredient, scale, excluded, catalog, ctx, chain):
    """Resolve one ingredient (and any it introduces) against the exclusions.

    Returns a list of adapted ingredient dicts, or None if resolution failed;
    failure details are recorded in ctx.reasons.
    """
    ing = dict(ingredient)
    ing["quantity"] = Fraction(ing["quantity"]) * scale
    chain = list(chain)
    extras = []
    while ing["name"] in excluded:
        if ing["name"] in chain:
            ctx.reasons.add("substitution cycle involving %s" % ing["name"])
            return None
        chain.append(ing["name"])
        candidates = _sorted_candidates(catalog, ing["name"])
        if not candidates:
            ctx.reasons.add("no substitution for %s" % ing["name"])
            return None
        choice = candidates[0]
        ctx.choices.append(choice["id"])
        result = choice["result"]
        previous = ing["name"]
        ing["name"] = result["name"]
        if "quantity_factor" in result:
            ing["quantity"] *= Fraction(result["quantity_factor"])
        if "unit" in result:
            ing["unit"] = result["unit"]
        if "preparation" in result:
            ing["preparation"] = result["preparation"]
        if "yield_factor" in result:
            ctx.yield_corr /= Fraction(result["yield_factor"])
            ctx.warnings.add(
                "quantities rescaled to hold target yield after substituting %s"
                % ing["name"])
        ctx.wording.extend(result.get("wording_changes", []))
        ctx.equip_add.update(result.get("add_equipment", []))
        ctx.equip_remove.update(result.get("remove_equipment", []))
        ctx.warnings.add("substituted %s for %s" % (ing["name"], previous))
        if ing["name"] in excluded:
            ctx.warnings.add(
                "substitution reintroduced excluded ingredient %s" % ing["name"])
        for extra in result.get("additional_ingredients", []):
            resolved = _adapt_ingredient(extra, scale, excluded, catalog,
                                         ctx, chain)
            if resolved is None:
                return None
            extras.extend(resolved)
    return [ing] + extras


def adapt(recipe, request, catalog):
    """Adapt *recipe* to the request, or report every reason it is impossible."""
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])
    ctx = _Ctx()

    ingredients = []
    for ingredient in recipe["ingredients"]:
        resolved = _adapt_ingredient(ingredient, scale, excluded, catalog,
                                     ctx, [])
        if resolved is not None:
            ingredients.extend(resolved)

    components = []
    for component in recipe.get("components", []):
        local = _Ctx()
        adapted_ings = []
        failed = False
        for ingredient in component.get("ingredients", []):
            resolved = _adapt_ingredient(ingredient, scale, excluded, catalog,
                                         local, [])
            if resolved is None:
                failed = True
            else:
                adapted_ings.extend(resolved)
        if failed:
            ctx.warnings.add(
                "omitted optional component %s" % component.get("name", "?"))
            continue
        ctx.merge(local)
        components.append((component, adapted_ings))

    equipment = sorted((set(recipe["equipment"]) | ctx.equip_add)
                       - ctx.equip_remove)
    for item in equipment:
        if item not in available:
            ctx.reasons.add("equipment %s unavailable" % item)

    if ctx.reasons:
        return {"possible": False,
                "recipe": None,
                "choices": ctx.choices,
                "warnings": sorted(ctx.warnings),
                "reasons": sorted(ctx.reasons)}

    if ctx.yield_corr != 1:
        ingredients = [dict(ing, quantity=ing["quantity"] * ctx.yield_corr)
                       for ing in ingredients]
        components = [(comp, [dict(ing, quantity=ing["quantity"] * ctx.yield_corr)
                              for ing in ings])
                      for comp, ings in components]

    def rewrite(text):
        for change in ctx.wording:
            text = text.replace(change["old"], change["new"])
        return text

    adapted_components = []
    for component, ings in components:
        adapted = dict(component)
        adapted["ingredients"] = ings
        if "instructions" in adapted:
            adapted["instructions"] = [rewrite(s)
                                       for s in adapted["instructions"]]
        adapted_components.append(adapted)

    adapted_recipe = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": ingredients,
        "equipment": equipment,
        "instructions": [rewrite(s) for s in recipe["instructions"]],
        "components": adapted_components,
        "authored_text": recipe["authored_text"],
    }
    return {"possible": True,
            "recipe": adapted_recipe,
            "choices": ctx.choices,
            "warnings": sorted(ctx.warnings),
            "reasons": []}


if __name__ == "__main__":
    import copy

    def demo_recipe():
        return {
            "title": "Pancakes",
            "yield": Fraction(3),
            "ingredients": [
                {"id": "flour", "name": "flour", "quantity": Fraction(3, 2),
                 "unit": "cup", "preparation": "sifted"},
                {"id": "butter", "name": "butter", "quantity": Fraction(3),
                 "unit": "tbsp", "preparation": "melted"},
            ],
            "equipment": ["skillet", "whisk"],
            "instructions": ["Whisk the sifted flour.",
                             "Melt the butter in the skillet."],
            "components": [],
            "authored_text": "Pancakes\nServes 3\nWhisk; melt; fry.\n",
        }

    everything = ["skillet", "whisk", "saucepan"]

    # 1. Scaling 3 -> 5 servings.
    recipe = demo_recipe()
    out = adapt(recipe, {"target_yield": Fraction(5),
                         "available_equipment": everything,
                         "excluded": []}, [])
    assert out["possible"] and out["recipe"]["yield"] == Fraction(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == Fraction(5, 2)
    assert out["recipe"]["ingredients"][1]["quantity"] == Fraction(5)

    # 2. Chained substitution (butter -> margarine -> oil) and
    #    reintroduced-exclusion detection.
    catalog = [
        {"id": "c1", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "c2", "for": "margarine", "priority": 1,
         "result": {"name": "oil", "unit": "tbsp"}},
    ]
    out = adapt(demo_recipe(), {"target_yield": Fraction(3),
                                "available_equipment": everything,
                                "excluded": ["butter", "margarine"]}, catalog)
    assert out["possible"] and out["choices"] == ["c1", "c2"]
    assert out["recipe"]["ingredients"][1]["name"] == "oil"
    assert any("reintroduced" in w for w in out["warnings"])

    # 3. Substitution changing a referenced verb and equipment.
    catalog = [{"id": "v1", "for": "butter", "priority": 1,
                "result": {"name": "oil", "preparation": "poured",
                           "wording_changes": [{"old": "Melt", "new": "Pour"},
                                               {"old": "butter", "new": "oil"},
                                               {"old": "skillet",
                                                "new": "saucepan"}],
                           "add_equipment": ["saucepan"],
                           "remove_equipment": ["skillet"]}}]
    out = adapt(demo_recipe(), {"target_yield": Fraction(3),
                                "available_equipment": ["whisk", "saucepan"],
                                "excluded": ["butter"]}, catalog)
    assert out["possible"]
    assert out["recipe"]["instructions"][1] == "Pour the oil in the saucepan."
    assert out["recipe"]["equipment"] == ["saucepan", "whisk"]

    # 4. Substitution cycle.
    catalog = [
        {"id": "x1", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "x2", "for": "margarine", "priority": 1,
         "result": {"name": "butter"}},
    ]
    out = adapt(demo_recipe(), {"target_yield": Fraction(3),
                                "available_equipment": everything,
                                "excluded": ["butter", "margarine"]}, catalog)
    assert not out["possible"] and out["recipe"] is None
    assert "substitution cycle involving butter" in out["reasons"]

    # 5. Multiple simultaneous impossibility reasons.
    out = adapt(demo_recipe(), {"target_yield": Fraction(3),
                                "available_equipment": ["whisk"],
                                "excluded": ["flour"]}, [])
    assert not out["possible"]
    assert out["reasons"] == sorted(["equipment skillet unavailable",
                                     "no substitution for flour"])

    # 6. Exact original print after adaptations; inputs unmutated.
    recipe = demo_recipe()
    snapshot = copy.deepcopy(recipe)
    adapt(recipe, {"target_yield": Fraction(5),
                   "available_equipment": everything,
                   "excluded": []}, [])
    assert recipe == snapshot
    assert print_original(recipe) == "Pancakes\nServes 3\nWhisk; melt; fry.\n"

    # 7. Deterministic choice by explicit priority, catalog order tie-break.
    catalog = [
        {"id": "p2", "for": "butter", "priority": 2,
         "result": {"name": "lard"}},
        {"id": "p1", "for": "butter", "priority": 1,
         "result": {"name": "oil"}},
        {"id": "p1b", "for": "butter", "priority": 1,
         "result": {"name": "ghee"}},
    ]
    out = adapt(demo_recipe(), {"target_yield": Fraction(3),
                                "available_equipment": everything,
                                "excluded": ["butter"]}, catalog)
    assert out["choices"] == ["p1"]
    assert out["recipe"]["ingredients"][1]["name"] == "oil"

    print("all acceptance checks passed")
