"""recipebook.py — adapt recipes to yield, equipment, exclusions, and a substitution catalog."""

from fractions import Fraction
import copy


def print_original(recipe):
    return recipe["authored_text"]


def _best_choice(name, catalog):
    best_key = None
    best = None
    for index, choice in enumerate(catalog):
        if choice["for"] != name:
            continue
        key = (choice["priority"], index)
        if best_key is None or key < best_key:
            best_key = key
            best = choice
    return best


def adapt(recipe, request, catalog):
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])

    reasons = set()
    choices = []
    warnings = []
    instructions = list(recipe["instructions"])
    equipment = list(recipe["equipment"])

    pending = [
        {
            "id": ing["id"],
            "name": ing["name"],
            "quantity": ing["quantity"] * scale,
            "unit": ing["unit"],
            "preparation": ing["preparation"],
        }
        for ing in recipe["ingredients"]
    ]

    final_ingredients = []
    yield_adjust = Fraction(1)

    index = 0
    while index < len(pending):
        ing = pending[index]
        index += 1
        chain = [ing["name"]]
        viable = True
        while ing["name"] in excluded:
            choice = _best_choice(ing["name"], catalog)
            if choice is None:
                reasons.add("no substitution for " + ing["name"])
                viable = False
                break
            choices.append(choice["id"])
            result = choice["result"]
            new_name = result["name"]
            if new_name in chain:
                reasons.add("substitution cycle involving " + new_name)
                viable = False
                break
            ing["name"] = new_name
            ing["quantity"] = ing["quantity"] * result.get("quantity_factor", 1)
            if "unit" in result:
                ing["unit"] = result["unit"]
            if "preparation" in result:
                ing["preparation"] = result["preparation"]
            for change in result.get("wording_changes", []):
                if any(change["old"] in line for line in instructions):
                    instructions = [line.replace(change["old"], change["new"])
                                    for line in instructions]
                else:
                    warnings.append("wording '" + change["old"] + "' not found")
            for item in result.get("remove_equipment", []):
                equipment = [e for e in equipment if e != item]
            for item in result.get("add_equipment", []):
                if item not in equipment:
                    equipment.append(item)
            if "yield_factor" in result:
                yield_adjust *= Fraction(result["yield_factor"])
                warnings.append("yield adjusted by " + choice["id"])
            for extra in result.get("additional_ingredients", []):
                pending.append(
                    {
                        "id": extra["id"],
                        "name": extra["name"],
                        "quantity": extra["quantity"] * scale,
                        "unit": extra["unit"],
                        "preparation": extra["preparation"],
                    }
                )
            chain.append(new_name)
            if len(chain) == 3:
                warnings.append("chained substitution for " + chain[0])
        if viable:
            final_ingredients.append(ing)

    for item in equipment:
        if item not in available:
            reasons.add("equipment " + item + " unavailable")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    if yield_adjust != 1:
        for ing in final_ingredients:
            ing["quantity"] = ing["quantity"] / yield_adjust

    adapted = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": final_ingredients,
        "equipment": sorted(equipment),
        "instructions": instructions,
        "components": copy.deepcopy(recipe["components"]),
        "authored_text": recipe["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }


# ---------------------------------------------------------------------------
# Executable acceptance contract
# ---------------------------------------------------------------------------

def _base_recipe():
    F = Fraction
    return {
        "title": "Herb Butter Pasta",
        "yield": F(3),
        "ingredients": [
            {"id": "pasta", "name": "pasta", "quantity": F(300), "unit": "g",
             "preparation": "boiled"},
            {"id": "butter", "name": "butter", "quantity": F(3, 2), "unit": "tbsp",
             "preparation": "melted"},
            {"id": "cream", "name": "cream", "quantity": F(1, 2), "unit": "cup",
             "preparation": "whipped"},
        ],
        "equipment": ["pot", "whisk"],
        "instructions": [
            "Boil the pasta in a pot.",
            "Whip the cream with a whisk.",
            "Melt the butter and toss.",
        ],
        "components": [{"name": "garnish", "optional": True, "ingredient_ids": ["butter"]}],
        "authored_text": "Herb Butter Pasta\nServes 3\nBoil, whip, melt, toss.\n",
    }


def _run_tests():
    F = Fraction
    all_equipment = ["pot", "whisk", "blender"]

    # 1. Scaling 3 -> 5 servings.
    out = adapt(_base_recipe(),
                {"target_yield": F(5), "available_equipment": all_equipment, "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    qty = {i["id"]: i["quantity"] for i in out["recipe"]["ingredients"]}
    assert qty == {"pasta": F(500), "butter": F(5, 2), "cream": F(5, 6)}
    assert out["recipe"]["yield"] == F(5)

    # 2. Chained substitution: butter -> margarine -> oil, with factor and unit change.
    catalog = [
        {"id": "sub-butter-margarine", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "sub-margarine-oil", "for": "margarine", "priority": 1,
         "result": {"name": "oil", "quantity_factor": F(3, 4), "unit": "tbsp"}},
    ]
    out = adapt(_base_recipe(),
                {"target_yield": F(3), "available_equipment": all_equipment,
                 "excluded": ["butter", "margarine"]}, catalog)
    assert out["possible"]
    assert out["choices"] == ["sub-butter-margarine", "sub-margarine-oil"]
    oil = next(i for i in out["recipe"]["ingredients"] if i["id"] == "butter")
    assert oil["name"] == "oil" and oil["quantity"] == F(9, 8) and oil["unit"] == "tbsp"
    assert "chained substitution for butter" in out["warnings"]

    # 3. Substitution changing a referenced verb and equipment.
    catalog = [
        {"id": "sub-cream-coconut", "for": "cream", "priority": 1, "result": {
            "name": "coconut cream", "preparation": "chilled",
            "wording_changes": [{"old": "Whip", "new": "Chill"},
                                {"old": "with a whisk", "new": "in a blender"}],
            "remove_equipment": ["whisk"], "add_equipment": ["blender"],
        }},
    ]
    out = adapt(_base_recipe(),
                {"target_yield": F(3), "available_equipment": ["pot", "blender"],
                 "excluded": ["cream"]}, catalog)
    assert out["possible"]
    assert "Chill the cream in a blender." in out["recipe"]["instructions"]
    assert out["recipe"]["equipment"] == ["blender", "pot"]
    cc = next(i for i in out["recipe"]["ingredients"] if i["id"] == "cream")
    assert cc["name"] == "coconut cream" and cc["preparation"] == "chilled"

    # 4. Exclusion reintroduced later: butter -> lard, lard also excluded, no entry.
    catalog = [{"id": "sub-butter-lard", "for": "butter", "priority": 1,
                "result": {"name": "lard"}}]
    out = adapt(_base_recipe(),
                {"target_yield": F(3), "available_equipment": all_equipment,
                 "excluded": ["butter", "lard"]}, catalog)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["no substitution for lard"]
    assert out["choices"] == ["sub-butter-lard"]

    # 4b. Reintroduction via an additional ingredient, then resolved by the catalog.
    catalog = [
        {"id": "sub-cream-cashew", "for": "cream", "priority": 1, "result": {
            "name": "cashew cream",
            "additional_ingredients": [
                {"id": "nuts", "name": "peanuts", "quantity": F(1, 4), "unit": "cup",
                 "preparation": "soaked"}],
        }},
        {"id": "sub-peanut-cashew", "for": "peanuts", "priority": 1,
         "result": {"name": "cashews"}},
    ]
    out = adapt(_base_recipe(),
                {"target_yield": F(3), "available_equipment": all_equipment,
                 "excluded": ["cream", "peanuts"]}, catalog)
    assert out["possible"]
    assert out["choices"] == ["sub-cream-cashew", "sub-peanut-cashew"]
    assert any(i["name"] == "cashews" for i in out["recipe"]["ingredients"])

    # 5. Multiple simultaneous impossibility reasons, all reported and sorted.
    out = adapt(_base_recipe(),
                {"target_yield": F(3), "available_equipment": ["pot"],
                 "excluded": ["cream"]}, [])
    assert not out["possible"]
    assert out["reasons"] == ["equipment whisk unavailable", "no substitution for cream"]

    # 5b. Substitution cycle.
    catalog = [
        {"id": "c1", "for": "butter", "priority": 1, "result": {"name": "margarine"}},
        {"id": "c2", "for": "margarine", "priority": 1, "result": {"name": "butter"}},
    ]
    out = adapt(_base_recipe(),
                {"target_yield": F(3), "available_equipment": all_equipment,
                 "excluded": ["butter", "margarine"]}, catalog)
    assert not out["possible"]
    assert out["reasons"] == ["substitution cycle involving butter"]

    # 6. Exact original print and no input mutation after an adaptation.
    r = _base_recipe()
    req = {"target_yield": F(5), "available_equipment": all_equipment, "excluded": ["butter"]}
    cat = [{"id": "sub", "for": "butter", "priority": 1,
            "result": {"name": "oil", "wording_changes": [{"old": "butter", "new": "oil"}]}}]
    snap_r, snap_req, snap_cat = copy.deepcopy(r), copy.deepcopy(req), copy.deepcopy(cat)
    adapt(r, req, cat)
    assert r == snap_r and req == snap_req and cat == snap_cat
    assert print_original(r) == "Herb Butter Pasta\nServes 3\nBoil, whip, melt, toss.\n"

    # 7. Deterministic choice: lowest priority wins, catalog order breaks ties.
    catalog = [
        {"id": "b-high", "for": "butter", "priority": 5, "result": {"name": "ghee"}},
        {"id": "b-low-first", "for": "butter", "priority": 1, "result": {"name": "oil"}},
        {"id": "b-low-second", "for": "butter", "priority": 1, "result": {"name": "margarine"}},
    ]
    out = adapt(_base_recipe(),
                {"target_yield": F(3), "available_equipment": all_equipment,
                 "excluded": ["butter"]}, catalog)
    assert out["possible"] and out["choices"] == ["b-low-first"]
    assert any(i["name"] == "oil" for i in out["recipe"]["ingredients"])

    print("all tests passed")


if __name__ == "__main__":
    _run_tests()
