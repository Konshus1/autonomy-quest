"""recipebook.py — adapt structured recipes to a target yield, available
equipment, dietary exclusions, and a substitution catalog.

Quantities and yields are fractions.Fraction; only the standard library is used.
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the authored text exactly as written."""
    return recipe["authored_text"]


def _best_choice(name, catalog):
    """Lowest numeric priority wins; catalog order breaks ties."""
    best = None
    best_key = None
    for index, choice in enumerate(catalog):
        if choice["for"] != name:
            continue
        key = (choice["priority"], index)
        if best_key is None or key < best_key:
            best_key, best = key, choice
    return best


def adapt(recipe, request, catalog):
    work = copy.deepcopy(recipe)
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])

    choices = []
    warnings = []
    reasons = set()
    renames = []       # (old name, new name) in application order
    rewordings = []    # (old, new) from selected wording_changes
    equipment = set(work["equipment"])
    yield_correction = Fraction(1)

    def ingredient_groups():
        yield work["ingredients"]
        for component in work.get("components") or []:
            if "ingredients" in component:
                yield component["ingredients"]

    def instruction_lists():
        yield work["instructions"]
        for component in work.get("components") or []:
            if "instructions" in component:
                yield component["instructions"]

    for group in ingredient_groups():
        for ingredient in group:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    def resolve(ingredient, group):
        nonlocal yield_correction
        seen = set()
        while ingredient["name"] in excluded:
            if ingredient["name"] in seen:
                reasons.add("substitution cycle involving " + ingredient["name"])
                return
            seen.add(ingredient["name"])
            choice = _best_choice(ingredient["name"], catalog)
            if choice is None:
                reasons.add("no substitution for " + ingredient["name"])
                return
            choices.append(choice["id"])
            result = choice["result"]
            old_name = ingredient["name"]
            renames.append((old_name, result["name"]))
            ingredient["name"] = result["name"]
            if "quantity_factor" in result:
                ingredient["quantity"] *= Fraction(result["quantity_factor"])
            if "unit" in result:
                ingredient["unit"] = result["unit"]
            if "preparation" in result:
                ingredient["preparation"] = result["preparation"]
            elif "preparation" in ingredient:
                ingredient["preparation"] = ingredient["preparation"].replace(
                    old_name, result["name"])
            for pair in result.get("wording_changes", []):
                rewordings.append((pair["old"], pair["new"]))
            for item in result.get("add_equipment", []):
                equipment.add(item)
            for item in result.get("remove_equipment", []):
                equipment.discard(item)
            if "yield_factor" in result:
                yield_correction /= Fraction(result["yield_factor"])
                warnings.append(
                    "substitution %s alters yield; quantities rescaled to target"
                    % choice["id"])
            for extra in result.get("additional_ingredients", []):
                extra = copy.deepcopy(extra)
                extra["quantity"] = Fraction(extra["quantity"]) * scale
                group.append(extra)
                if extra["name"] in excluded:
                    warnings.append(
                        "substitution %s introduces excluded ingredient %s"
                        % (choice["id"], extra["name"]))
                resolve(extra, group)
            if ingredient["name"] in excluded:
                warnings.append(
                    "substitution %s introduces excluded ingredient %s"
                    % (choice["id"], ingredient["name"]))

    for group in ingredient_groups():
        for ingredient in list(group):
            resolve(ingredient, group)

    if yield_correction != 1:
        for group in ingredient_groups():
            for ingredient in group:
                ingredient["quantity"] *= yield_correction

    for item in equipment - available:
        reasons.add("equipment %s unavailable" % item)

    def rewrite(text):
        for old, new in renames:
            text = text.replace(old, new)
        for old, new in rewordings:
            text = text.replace(old, new)
        return text

    for lines in instruction_lists():
        for i, line in enumerate(lines):
            lines[i] = rewrite(line)

    warnings.sort()
    if reasons:
        return {"possible": False, "recipe": None, "choices": choices,
                "warnings": warnings, "reasons": sorted(reasons)}

    work["yield"] = target
    work["equipment"] = sorted(equipment)
    return {"possible": True, "recipe": work, "choices": choices,
            "warnings": warnings, "reasons": []}


def _base_recipe():
    return {
        "title": "Herb Butter Pasta",
        "yield": Fraction(3),
        "ingredients": [
            {"id": "i1", "name": "butter", "quantity": Fraction(6),
             "unit": "tbsp", "preparation": "melt the butter"},
            {"id": "i2", "name": "pasta", "quantity": Fraction(9),
             "unit": "oz", "preparation": "boil the pasta"},
            {"id": "i3", "name": "parsley", "quantity": Fraction(3, 2),
             "unit": "tbsp", "preparation": "chop the parsley"},
        ],
        "equipment": ["pot", "skillet"],
        "instructions": [
            "melt the butter in the skillet",
            "boil the pasta in the pot",
            "chop the parsley and toss everything together",
        ],
        "components": [
            {"name": "garnish", "optional": True,
             "ingredients": [
                 {"id": "g1", "name": "lemon zest", "quantity": Fraction(1),
                  "unit": "tsp", "preparation": "zest the lemon"}],
             "instructions": ["zest the lemon over the top"]},
        ],
        "authored_text": (
            "Herb Butter Pasta\nServes 3.\n"
            "Melt 6 tbsp butter in the skillet.\n"),
    }


if __name__ == "__main__":
    F = Fraction

    # 1. Scaling 3 -> 5 servings.
    out = adapt(_base_recipe(),
                {"target_yield": F(5),
                 "available_equipment": ["pot", "skillet"],
                 "excluded": []}, [])
    assert out["possible"] and out["reasons"] == [] and out["choices"] == []
    q = {i["name"]: i["quantity"] for i in out["recipe"]["ingredients"]}
    assert q == {"butter": F(10), "pasta": F(15), "parsley": F(5, 2)}
    assert out["recipe"]["components"][0]["ingredients"][0]["quantity"] == F(5, 3)
    assert out["recipe"]["yield"] == F(5)

    # 2. Chained substitution: butter -> margarine (also excluded) -> olive oil.
    cat = [
        {"id": "c1", "for": "butter", "priority": 1,
         "result": {"name": "margarine", "quantity_factor": F(3, 4)}},
        {"id": "c2", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "unit": "tbsp"}},
    ]
    out = adapt(_base_recipe(),
                {"target_yield": F(3),
                 "available_equipment": ["pot", "skillet"],
                 "excluded": ["butter", "margarine"]}, cat)
    assert out["possible"] and out["choices"] == ["c1", "c2"]
    fat = out["recipe"]["ingredients"][0]
    assert fat["name"] == "olive oil" and fat["quantity"] == F(9, 2)
    assert out["warnings"] == [
        "substitution c1 introduces excluded ingredient margarine"]

    # 3. Substitution changing a referenced verb and equipment.
    cat = [{"id": "c3", "for": "butter", "priority": 1,
            "result": {"name": "olive oil",
                       "preparation": "warm the olive oil",
                       "wording_changes": [{"old": "melt", "new": "warm"},
                                           {"old": "skillet", "new": "saucepan"}],
                       "add_equipment": ["saucepan"],
                       "remove_equipment": ["skillet"]}}]
    out = adapt(_base_recipe(),
                {"target_yield": F(3),
                 "available_equipment": ["pot", "saucepan"],
                 "excluded": ["butter"]}, cat)
    assert out["possible"]
    assert out["recipe"]["instructions"][0] == "warm the olive oil in the saucepan"
    assert out["recipe"]["ingredients"][0]["preparation"] == "warm the olive oil"
    assert out["recipe"]["equipment"] == ["pot", "saucepan"]

    # 4. Exclusion reintroduced by an additional ingredient, resolved by chaining.
    cat = [
        {"id": "v1", "for": "butter", "priority": 1,
         "result": {"name": "vegan spread",
                    "additional_ingredients": [
                        {"id": "x1", "name": "soy lecithin",
                         "quantity": F(1), "unit": "tsp",
                         "preparation": "stir in the soy lecithin"}]}},
        {"id": "v2", "for": "soy lecithin", "priority": 1,
         "result": {"name": "sunflower lecithin"}},
    ]
    out = adapt(_base_recipe(),
                {"target_yield": F(3),
                 "available_equipment": ["pot", "skillet"],
                 "excluded": ["butter", "soy lecithin"]}, cat)
    assert out["possible"] and out["choices"] == ["v1", "v2"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "sunflower lecithin" in names and "soy lecithin" not in names
    assert out["warnings"] == [
        "substitution v1 introduces excluded ingredient soy lecithin"]
    assert out["recipe"]["ingredients"][-1]["preparation"] == \
        "stir in the sunflower lecithin"

    # 5. Multiple simultaneous impossibility reasons.
    cat = [
        {"id": "y1", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "y2", "for": "margarine", "priority": 1,
         "result": {"name": "butter"}},
    ]
    out = adapt(_base_recipe(),
                {"target_yield": F(3),
                 "available_equipment": ["pot"],
                 "excluded": ["butter", "margarine", "parsley"]}, cat)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["equipment skillet unavailable",
                              "no substitution for parsley",
                              "substitution cycle involving butter"]

    # 6. Original prints byte-for-byte after adaptation; inputs unmutated.
    r = _base_recipe()
    snap = copy.deepcopy(r)
    req = {"target_yield": F(5),
           "available_equipment": ["pot", "saucepan"],
           "excluded": ["butter"]}
    cat = [{"id": "c3", "for": "butter", "priority": 1,
            "result": {"name": "olive oil",
                       "wording_changes": [{"old": "skillet", "new": "saucepan"}],
                       "add_equipment": ["saucepan"],
                       "remove_equipment": ["skillet"]}}]
    reqsnap, catsnap = copy.deepcopy(req), copy.deepcopy(cat)
    out = adapt(r, req, cat)
    assert out["possible"]
    assert r == snap and req == reqsnap and cat == catsnap
    assert print_original(r) == snap["authored_text"]

    # 7. Deterministic choice: explicit priority, catalog order as tie-breaker.
    cat = [
        {"id": "p2", "for": "butter", "priority": 2,
         "result": {"name": "ghee"}},
        {"id": "p1a", "for": "butter", "priority": 1,
         "result": {"name": "olive oil"}},
        {"id": "p1b", "for": "butter", "priority": 1,
         "result": {"name": "coconut oil"}},
    ]
    out = adapt(_base_recipe(),
                {"target_yield": F(3),
                 "available_equipment": ["pot", "skillet"],
                 "excluded": ["butter"]}, cat)
    assert out["choices"] == ["p1a"]
    assert out["recipe"]["ingredients"][0]["name"] == "olive oil"

    print("all acceptance checks passed")
