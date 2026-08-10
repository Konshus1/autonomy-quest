"""recipebook: adapt authored recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog, using only the stdlib."""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the authored text exactly as written."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    work = copy.deepcopy(recipe)
    target = Fraction(request["target_yield"])
    available = set(request.get("available_equipment", []))
    excluded = set(request.get("excluded", []))

    reasons = set()
    warnings = set()
    choices = []

    scale = target / Fraction(work["yield"])

    options_for = {}
    for index, choice in enumerate(catalog):
        options_for.setdefault(choice["for"], []).append(
            (choice.get("priority", 0), index, choice))
    for opts in options_for.values():
        opts.sort(key=lambda entry: (entry[0], entry[1]))

    equipment = set(work.get("equipment", []))
    wording_changes = []
    yield_factors = []
    extra_queue = []

    def resolve(ing):
        chain = [ing["name"]]
        while ing["name"] in excluded:
            opts = options_for.get(ing["name"])
            if not opts:
                reasons.add("no substitution for " + ing["name"])
                return
            choice = opts[0][2]
            result = choice["result"]
            choices.append(choice["id"])
            new_name = result["name"]
            if new_name in chain:
                reasons.add("substitution cycle involving " + new_name)
                return
            warnings.add("replaced %s with %s" % (ing["name"], new_name))
            ing["name"] = new_name
            if "quantity_factor" in result:
                ing["quantity"] = ing["quantity"] * Fraction(result["quantity_factor"])
            if "unit" in result:
                ing["unit"] = result["unit"]
            if "preparation" in result:
                ing["preparation"] = result["preparation"]
            for pair in result.get("wording_changes", []):
                wording_changes.append((pair["old"], pair["new"]))
            for name in result.get("add_equipment", []):
                equipment.add(name)
            for name in result.get("remove_equipment", []):
                equipment.discard(name)
            if "yield_factor" in result:
                yield_factors.append((choice["id"], Fraction(result["yield_factor"])))
            for extra in result.get("additional_ingredients", []):
                extra_queue.append({
                    "id": extra.get("id", extra["name"]),
                    "name": extra["name"],
                    "quantity": Fraction(extra.get("quantity", 1)) * scale,
                    "unit": extra.get("unit", ""),
                    "preparation": extra.get("preparation", ""),
                })
            chain.append(new_name)

    ingredient_lists = [work.setdefault("ingredients", [])]
    for component in work.get("components", []):
        ingredient_lists.append(component.setdefault("ingredients", []))

    for lst in ingredient_lists:
        for ing in lst:
            ing["quantity"] = Fraction(ing["quantity"]) * scale

    for lst in ingredient_lists:
        for ing in lst:
            resolve(ing)

    # Additional ingredients face the same exclusions and catalog choices.
    while extra_queue:
        added = extra_queue.pop(0)
        resolve(added)
        work["ingredients"].append(added)

    compensation = Fraction(1)
    for choice_id, factor in yield_factors:
        compensation *= factor
        warnings.add("yield rescaled after substitution " + str(choice_id))
    if compensation != 1:
        for lst in ingredient_lists:
            for ing in lst:
                ing["quantity"] = ing["quantity"] / compensation

    for name in sorted(equipment):
        if name not in available:
            reasons.add("equipment %s unavailable" % name)

    if reasons:
        return {"possible": False, "recipe": None, "choices": choices,
                "warnings": sorted(warnings), "reasons": sorted(reasons)}

    def rewrite(lines):
        out = []
        for line in lines:
            for old, new in wording_changes:
                line = line.replace(old, new)
            out.append(line)
        return out

    work["yield"] = target
    work["equipment"] = sorted(equipment)
    work["instructions"] = rewrite(work.get("instructions", []))
    for component in work.get("components", []):
        component["instructions"] = rewrite(component.get("instructions", []))

    return {"possible": True, "recipe": work, "choices": choices,
            "warnings": sorted(warnings), "reasons": []}


if __name__ == "__main__":
    def make_recipe():
        return {
            "title": "Pancakes",
            "yield": Fraction(3),
            "ingredients": [
                {"id": "flour", "name": "wheat flour",
                 "quantity": Fraction(300), "unit": "g", "preparation": "sifted"},
                {"id": "milk", "name": "milk",
                 "quantity": Fraction(1, 2), "unit": "l", "preparation": ""},
                {"id": "butter", "name": "butter",
                 "quantity": Fraction(30), "unit": "g", "preparation": "melted"},
            ],
            "equipment": ["whisk", "skillet"],
            "instructions": [
                "Whisk the sifted wheat flour with the milk.",
                "Melt the butter in the skillet.",
            ],
            "components": [],
            "authored_text": "Pancakes\nServes 3\n300 g wheat flour, sifted\n"
                             "1/2 l milk\n30 g butter, melted\n"
                             "Whisk the sifted wheat flour with the milk.\n"
                             "Melt the butter in the skillet.\n",
        }

    gear = ["whisk", "skillet", "saucepan"]

    # 1. Scaling 3 -> 5 servings.
    out = adapt(make_recipe(),
                {"target_yield": Fraction(5), "available_equipment": gear,
                 "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    got = {i["id"]: i["quantity"] for i in out["recipe"]["ingredients"]}
    assert got == {"flour": Fraction(500), "milk": Fraction(5, 6),
                   "butter": Fraction(50)}
    assert out["recipe"]["yield"] == Fraction(5)

    # 2. Chained substitution: milk -> soy milk -> oat milk.
    chain_catalog = [
        {"id": "c1", "for": "milk", "priority": 1,
         "result": {"name": "soy milk", "quantity_factor": Fraction(1)}},
        {"id": "c2", "for": "soy milk", "priority": 1,
         "result": {"name": "oat milk", "quantity_factor": Fraction(2)}},
    ]
    out = adapt(make_recipe(),
                {"target_yield": Fraction(3), "available_equipment": gear,
                 "excluded": ["milk", "soy milk"]}, chain_catalog)
    assert out["possible"] and out["choices"] == ["c1", "c2"]
    milk = next(i for i in out["recipe"]["ingredients"] if i["id"] == "milk")
    assert milk["name"] == "oat milk" and milk["quantity"] == Fraction(1)

    # 3. Substitution changing a referenced verb and equipment.
    verb_catalog = [
        {"id": "oil", "for": "butter", "priority": 1,
         "result": {"name": "sunflower oil", "unit": "ml", "preparation": "",
                    "wording_changes": [
                        {"old": "Melt the butter in the skillet.",
                         "new": "Warm the sunflower oil in the saucepan."}],
                    "add_equipment": ["saucepan"]}},
    ]
    out = adapt(make_recipe(),
                {"target_yield": Fraction(3), "available_equipment": gear,
                 "excluded": ["butter"]}, verb_catalog)
    assert out["possible"]
    assert "Warm the sunflower oil in the saucepan." in out["recipe"]["instructions"]
    assert out["recipe"]["equipment"] == sorted(out["recipe"]["equipment"])
    assert set(out["recipe"]["equipment"]) <= set(gear)
    assert "saucepan" in out["recipe"]["equipment"]

    # 4. Exclusion reintroduced by an additional ingredient.
    reintro_catalog = [
        {"id": "blend", "for": "milk", "priority": 1,
         "result": {"name": "nut blend",
                    "additional_ingredients": [
                        {"name": "almonds", "quantity": Fraction(50), "unit": "g"}]}},
    ]
    out = adapt(make_recipe(),
                {"target_yield": Fraction(3), "available_equipment": gear,
                 "excluded": ["milk", "almonds"]}, reintro_catalog)
    assert not out["possible"] and out["recipe"] is None
    assert "no substitution for almonds" in out["reasons"]

    # 5. Multiple simultaneous impossibility reasons.
    out = adapt(make_recipe(),
                {"target_yield": Fraction(3), "available_equipment": ["whisk"],
                 "excluded": ["butter"]}, [])
    assert not out["possible"]
    assert out["reasons"] == ["equipment skillet unavailable",
                              "no substitution for butter"]

    # Substitution cycle.
    cycle_catalog = [
        {"id": "a", "for": "milk", "priority": 1, "result": {"name": "soy milk"}},
        {"id": "b", "for": "soy milk", "priority": 1, "result": {"name": "milk"}},
    ]
    out = adapt(make_recipe(),
                {"target_yield": Fraction(3), "available_equipment": gear,
                 "excluded": ["milk", "soy milk"]}, cycle_catalog)
    assert not out["possible"]
    assert "substitution cycle involving milk" in out["reasons"]

    # 6. Exact original print after adaptations.
    original = make_recipe()
    pristine = copy.deepcopy(original)
    adapt(original, {"target_yield": Fraction(5), "available_equipment": gear,
                     "excluded": ["milk", "soy milk"]}, chain_catalog)
    assert original == pristine
    assert print_original(original) == pristine["authored_text"]

    # 7. Deterministic choice by explicit catalog priority, then order.
    priority_catalog = [
        {"id": "goat", "for": "milk", "priority": 5, "result": {"name": "goat milk"}},
        {"id": "oat", "for": "milk", "priority": 1, "result": {"name": "oat milk"}},
        {"id": "rice", "for": "milk", "priority": 1, "result": {"name": "rice milk"}},
    ]
    out = adapt(make_recipe(),
                {"target_yield": Fraction(3), "available_equipment": gear,
                 "excluded": ["milk"]}, priority_catalog)
    assert out["possible"] and out["choices"] == ["oat"]
    assert any(i["name"] == "oat milk" for i in out["recipe"]["ingredients"])

    print("all acceptance checks passed")
