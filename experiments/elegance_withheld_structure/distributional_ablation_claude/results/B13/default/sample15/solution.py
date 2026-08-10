"""recipebook.py -- recipe adaptation with rational quantities.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Neither function mutates its inputs.
"""

from fractions import Fraction

__all__ = ["adapt", "print_original"]


def print_original(recipe):
    """Return the recipe exactly as authored, byte-for-byte."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    excluded = set(request.get("excluded", ()))
    available = set(request.get("available_equipment", ()))
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / Fraction(recipe["yield"])

    by_name = {}
    for order, choice in enumerate(catalog):
        by_name.setdefault(choice["for"], []).append((choice["priority"], order, choice))

    reasons = set()
    warnings = set()
    choices = []
    wording = []  # committed (old, new) pairs, in application order
    equipment = list(recipe.get("equipment", ()))
    yield_factor = Fraction(1)

    def best(name):
        options = by_name.get(name)
        if not options:
            return None
        return min(options, key=lambda opt: (opt[0], opt[1]))[2]

    def resolve(ing):
        """Adapt one ingredient, chaining substitutions until nothing excluded
        remains.  Returns (adapted_ingredient_or_None, extra_ingredients).
        Side effects of the chain are committed only if the chain succeeds."""
        nonlocal yield_factor
        name = ing["name"]
        quantity = Fraction(ing["quantity"]) * scale
        unit = ing.get("unit")
        preparation = ing.get("preparation")
        pending_wording = []
        pending_add = []
        pending_remove = []
        pending_yield = Fraction(1)
        extras = []
        visited = [name]
        while name in excluded:
            choice = best(name)
            if choice is None:
                reasons.add("no substitution for " + name)
                return None, []
            result = choice["result"]
            choices.append(choice["id"])
            warnings.add("substituted %s for %s" % (result["name"], name))
            if "quantity_factor" in result:
                quantity *= Fraction(result["quantity_factor"])
            if "unit" in result:
                unit = result["unit"]
            if "preparation" in result:
                preparation = result["preparation"]
            if "yield_factor" in result:
                pending_yield *= Fraction(result["yield_factor"])
                warnings.add("yield changed by substitution for " + name)
            for wc in result.get("wording_changes", ()):
                pending_wording.append((wc["old"], wc["new"]))
            pending_add.extend(result.get("equipment_additions", ()))
            pending_remove.extend(result.get("equipment_removals", ()))
            extras.extend(result.get("additional_ingredients", ()))
            name = result["name"]
            if name in visited:
                reasons.add("substitution cycle involving " + visited[0])
                return None, []
            visited.append(name)
        wording.extend(pending_wording)
        for item in pending_add:
            if item not in equipment:
                equipment.append(item)
        for item in pending_remove:
            if item in equipment:
                equipment.remove(item)
        yield_factor *= pending_yield
        adapted = {
            "id": ing.get("id", ing["name"]),
            "name": name,
            "quantity": quantity,
            "unit": unit,
            "preparation": preparation,
        }
        return adapted, extras

    def process(ingredient_list):
        pending = list(ingredient_list)
        seen_extras = set()
        out = []
        i = 0
        while i < len(pending):
            adapted, extras = resolve(pending[i])
            i += 1
            if adapted is not None:
                out.append(adapted)
            for extra in extras:
                key = extra.get("id", extra["name"])
                if key not in seen_extras:
                    seen_extras.add(key)
                    pending.append(extra)
        return out

    ingredients = process(recipe.get("ingredients", ()))

    components = []
    for comp in recipe.get("components", ()):
        new_comp = dict(comp)
        if isinstance(comp.get("ingredients"), list):
            new_comp["ingredients"] = process(comp["ingredients"])
        components.append(new_comp)

    for item in equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    def reword(text):
        for old, new in wording:
            text = text.replace(old, new)
        return text

    for comp in components:
        if isinstance(comp.get("instructions"), list):
            comp["instructions"] = [reword(step) for step in comp["instructions"]]

    adapted_recipe = {
        "title": recipe["title"],
        "yield": target_yield * yield_factor,
        "ingredients": ingredients,
        "equipment": sorted(equipment),
        "instructions": [reword(step) for step in recipe.get("instructions", ())],
        "components": components,
        "authored_text": recipe["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted_recipe,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }


if __name__ == "__main__":
    import copy

    def base():
        return {
            "title": "Pound Cake",
            "yield": Fraction(3),
            "ingredients": [
                {"id": "flour", "name": "flour", "quantity": Fraction(2),
                 "unit": "cup", "preparation": "sifted"},
                {"id": "butter", "name": "butter", "quantity": Fraction(1, 2),
                 "unit": "cup", "preparation": "softened"},
            ],
            "equipment": ["mixer", "oven"],
            "instructions": ["Cream the butter until fluffy.", "Fold in the flour."],
            "components": [],
            "authored_text": ("Pound Cake\nServes 3\n"
                              "2 cup flour, sifted\n1/2 cup butter, softened\n"
                              "Cream the butter until fluffy.\nFold in the flour.\n"),
        }

    r = base()
    snapshot = copy.deepcopy(r)

    # 1. Scaling 3 -> 5 servings.
    out = adapt(r, {"target_yield": Fraction(5),
                    "available_equipment": ["mixer", "oven"], "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == Fraction(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == Fraction(10, 3)
    assert out["recipe"]["ingredients"][1]["quantity"] == Fraction(5, 6)
    assert out["recipe"]["equipment"] == ["mixer", "oven"]

    # 2. Chained substitution: butter -> margarine -> olive oil.
    chain_catalog = [
        {"id": "b2m", "for": "butter", "priority": 1, "result": {"name": "margarine"}},
        {"id": "m2o", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "quantity_factor": Fraction(3, 4), "unit": "cup"}},
    ]
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["mixer", "oven"],
                    "excluded": ["butter", "margarine"]}, chain_catalog)
    assert out["possible"]
    assert out["choices"] == ["b2m", "m2o"]
    fat = out["recipe"]["ingredients"][1]
    assert fat["name"] == "olive oil" and fat["quantity"] == Fraction(3, 8)
    assert out["warnings"] == sorted(out["warnings"])

    # 3. Substitution changing a referenced verb and equipment.
    verb_catalog = [
        {"id": "b2o", "for": "butter", "priority": 1,
         "result": {"name": "olive oil",
                    "wording_changes": [{"old": "Cream the butter",
                                         "new": "Whisk the olive oil"}],
                    "equipment_additions": ["whisk"],
                    "equipment_removals": ["mixer"]}},
    ]
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["whisk", "oven"],
                    "excluded": ["butter"]}, verb_catalog)
    assert out["possible"]
    assert out["recipe"]["instructions"][0] == "Whisk the olive oil until fluffy."
    assert out["recipe"]["equipment"] == ["oven", "whisk"]

    # 4. Substitution reintroduces an excluded ingredient.
    nut_catalog = [
        {"id": "b2n", "for": "butter", "priority": 1,
         "result": {"name": "nut spread",
                    "additional_ingredients": [
                        {"id": "peanuts", "name": "peanuts",
                         "quantity": Fraction(1, 4), "unit": "cup",
                         "preparation": "crushed"}]}},
    ]
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["mixer", "oven"],
                    "excluded": ["butter", "peanuts"]}, nut_catalog)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["no substitution for peanuts"]
    # ...and the chained fix once a further substitution exists.
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["mixer", "oven"],
                    "excluded": ["butter", "peanuts"]},
                nut_catalog + [{"id": "p2s", "for": "peanuts", "priority": 1,
                                "result": {"name": "sunflower seeds"}}])
    assert out["possible"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "sunflower seeds" in names and "peanuts" not in names
    assert out["choices"] == ["b2n", "p2s"]

    # 5. Multiple simultaneous impossibility reasons, sorted.
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["oven"], "excluded": ["butter"]}, [])
    assert not out["possible"]
    assert out["reasons"] == ["equipment mixer unavailable", "no substitution for butter"]

    # Cycle detection reason text.
    cycle_catalog = [
        {"id": "b2m", "for": "butter", "priority": 1, "result": {"name": "margarine"}},
        {"id": "m2b", "for": "margarine", "priority": 1, "result": {"name": "butter"}},
    ]
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["mixer", "oven"],
                    "excluded": ["butter", "margarine"]}, cycle_catalog)
    assert not out["possible"]
    assert "substitution cycle involving butter" in out["reasons"]

    # 6. Deterministic choice by explicit priority, catalog order as tie-break.
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["mixer", "oven"],
                    "excluded": ["butter"]},
                [{"id": "worse", "for": "butter", "priority": 5,
                  "result": {"name": "lard"}},
                 {"id": "better", "for": "butter", "priority": 1,
                  "result": {"name": "olive oil"}}])
    assert out["choices"] == ["better"]
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["mixer", "oven"],
                    "excluded": ["butter"]},
                [{"id": "first", "for": "butter", "priority": 1,
                  "result": {"name": "olive oil"}},
                 {"id": "second", "for": "butter", "priority": 1,
                  "result": {"name": "lard"}}])
    assert out["choices"] == ["first"]

    # 7. Exact original print after all adaptations; inputs never mutated.
    assert print_original(r) == snapshot["authored_text"]
    assert r == snapshot

    print("all acceptance checks passed")
