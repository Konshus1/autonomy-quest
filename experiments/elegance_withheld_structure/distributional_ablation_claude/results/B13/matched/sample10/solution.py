"""recipebook: yield scaling and prioritized substitution for authored recipes.

Recipes, requests, and catalogs are plain JSON-shaped dicts whose quantities
and yields are fractions.Fraction.  ``adapt`` never mutates its inputs and
``print_original`` returns the authored text byte-for-byte.
"""

from copy import deepcopy
from fractions import Fraction

__all__ = ["adapt", "print_original"]


def print_original(recipe):
    return recipe["authored_text"]


def _ranked(catalog, name):
    matches = [(c["priority"], i, c) for i, c in enumerate(catalog) if c["for"] == name]
    matches.sort(key=lambda m: (m[0], m[1]))
    return [c for _, _, c in matches]


def adapt(recipe, request, catalog):
    adapted = deepcopy(recipe)
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])

    chosen = []
    warnings = []
    reasons = set()
    wording = []
    equipment = set(adapted["equipment"])
    yield_scale = Fraction(1)

    adapted["yield"] = target
    ingredient_lists = [adapted["ingredients"]]
    instruction_lists = [adapted["instructions"]]
    for component in adapted.get("components") or []:
        if component.get("ingredients"):
            ingredient_lists.append(component["ingredients"])
        if component.get("instructions"):
            instruction_lists.append(component["instructions"])

    for ing_list in ingredient_lists:
        for ing in ing_list:
            ing["quantity"] = Fraction(ing["quantity"]) * scale

    def substitute(ing_list):
        nonlocal yield_scale
        queue = [(ing, ()) for ing in list(ing_list)]
        while queue:
            ing, ancestry = queue.pop(0)
            chain = list(ancestry)
            while ing["name"] in excluded:
                name = ing["name"]
                if name in chain:
                    reasons.add("substitution cycle involving " + name)
                    break
                chain.append(name)
                options = _ranked(catalog, name)
                if not options:
                    reasons.add("no substitution for " + name)
                    break
                choice = options[0]
                chosen.append(choice["id"])
                result = choice["result"]
                if result["name"] in excluded:
                    warnings.append(
                        "excluded {} reintroduced by {}".format(result["name"], choice["id"]))
                ing["name"] = result["name"]
                if "quantity_factor" in result:
                    ing["quantity"] *= Fraction(result["quantity_factor"])
                if "unit" in result:
                    ing["unit"] = result["unit"]
                if "preparation" in result:
                    ing["preparation"] = result["preparation"]
                if "yield_factor" in result and Fraction(result["yield_factor"]) != 1:
                    yield_scale *= Fraction(result["yield_factor"])
                    warnings.append(
                        "substitution {} changes yield; rescaled to target".format(choice["id"]))
                for pair in result.get("wording_changes", []):
                    wording.append((pair["old"], pair["new"]))
                for item in result.get("equipment_add", []):
                    equipment.add(item)
                for item in result.get("equipment_remove", []):
                    equipment.discard(item)
                for extra in result.get("additional_ingredients", []):
                    extra = deepcopy(extra)
                    extra.setdefault("id", extra["name"])
                    extra.setdefault("preparation", "")
                    extra["quantity"] = Fraction(extra["quantity"]) * scale
                    if extra["name"] in excluded:
                        warnings.append(
                            "excluded {} reintroduced by {}".format(extra["name"], choice["id"]))
                    ing_list.append(extra)
                    queue.append((extra, tuple(chain)))

    for ing_list in ingredient_lists:
        substitute(ing_list)

    for item in equipment:
        if item not in available:
            reasons.add("equipment " + item + " unavailable")

    if reasons:
        return {"possible": False, "recipe": None, "choices": chosen,
                "warnings": sorted(warnings), "reasons": sorted(reasons)}

    if yield_scale != 1:
        for ing_list in ingredient_lists:
            for ing in ing_list:
                ing["quantity"] /= yield_scale

    for instructions in instruction_lists:
        for i, text in enumerate(instructions):
            for old, new in wording:
                text = text.replace(old, new)
            instructions[i] = text

    adapted["equipment"] = sorted(equipment)
    return {"possible": True, "recipe": adapted, "choices": chosen,
            "warnings": sorted(warnings), "reasons": []}


if __name__ == "__main__":
    def sample():
        return {
            "title": "Herbed Butter Pasta",
            "yield": Fraction(3),
            "ingredients": [
                {"id": "pasta", "name": "pasta", "quantity": Fraction(300), "unit": "g",
                 "preparation": "boil the pasta"},
                {"id": "butter", "name": "butter", "quantity": Fraction(3, 2), "unit": "tbsp",
                 "preparation": "melt the butter"},
                {"id": "cream", "name": "cream", "quantity": Fraction(1, 2), "unit": "cup",
                 "preparation": "whip the cream"},
            ],
            "equipment": ["pot", "whisk"],
            "instructions": [
                "Melt the butter in the pot.",
                "Whip the cream with the whisk.",
                "Toss with pasta.",
            ],
            "components": [
                {"name": "garnish",
                 "ingredients": [{"id": "parsley", "name": "parsley", "quantity": Fraction(2),
                                  "unit": "tbsp", "preparation": "chop the parsley"}],
                 "instructions": ["Scatter the parsley."]},
            ],
            "authored_text": "Herbed Butter Pasta\nServes 3\nMelt the butter in the pot.\n",
        }

    everything = ["pot", "whisk", "blender", "saucepan"]

    # 1. scaling 3 -> 5 servings
    recipe = sample()
    frozen = deepcopy(recipe)
    out = adapt(recipe, {"target_yield": Fraction(5), "available_equipment": everything,
                         "excluded": []}, [])
    assert out["possible"] and out["reasons"] == [] and out["choices"] == []
    q = {i["id"]: i["quantity"] for i in out["recipe"]["ingredients"]}
    assert q == {"pasta": Fraction(500), "butter": Fraction(5, 2), "cream": Fraction(5, 6)}
    assert out["recipe"]["components"][0]["ingredients"][0]["quantity"] == Fraction(10, 3)
    assert out["recipe"]["yield"] == Fraction(5)

    # 2. chained substitution / 4. exclusion reintroduced later
    catalog = [
        {"id": "b2m", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "m2o", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "quantity_factor": Fraction(3, 4), "unit": "tbsp"}},
    ]
    out = adapt(recipe, {"target_yield": Fraction(3), "available_equipment": everything,
                         "excluded": ["butter", "margarine"]}, catalog)
    assert out["possible"] and out["choices"] == ["b2m", "m2o"]
    butter = next(i for i in out["recipe"]["ingredients"] if i["id"] == "butter")
    assert butter["name"] == "olive oil" and butter["quantity"] == Fraction(9, 8)
    assert "excluded margarine reintroduced by b2m" in out["warnings"]
    assert out["warnings"] == sorted(out["warnings"])

    # 3. substitution changing a referenced verb and equipment
    catalog = [{
        "id": "c2c", "for": "cream", "priority": 1,
        "result": {"name": "coconut cream", "preparation": "blend the coconut cream",
                   "wording_changes": [
                       {"old": "Whip the cream", "new": "Blend the coconut cream"},
                       {"old": "with the whisk", "new": "in the blender"}],
                   "equipment_add": ["blender"], "equipment_remove": ["whisk"]},
    }]
    out = adapt(recipe, {"target_yield": Fraction(3),
                         "available_equipment": ["pot", "blender"], "excluded": ["cream"]},
                catalog)
    assert out["possible"] and out["recipe"]["equipment"] == ["blender", "pot"]
    assert out["recipe"]["instructions"][1] == "Blend the coconut cream in the blender."

    # 5. multiple simultaneous impossibility reasons
    bad = {
        "title": "Impossible", "yield": Fraction(2),
        "ingredients": [
            {"id": "a", "name": "alpha", "quantity": Fraction(1), "unit": "cup",
             "preparation": "stir the alpha"},
            {"id": "g", "name": "gamma", "quantity": Fraction(1), "unit": "cup",
             "preparation": "stir the gamma"},
        ],
        "equipment": ["torch"], "instructions": ["Combine."], "components": [],
        "authored_text": "Impossible\n",
    }
    loop = [
        {"id": "a2b", "for": "alpha", "priority": 1, "result": {"name": "beta"}},
        {"id": "b2a", "for": "beta", "priority": 1, "result": {"name": "alpha"}},
    ]
    out = adapt(bad, {"target_yield": Fraction(2), "available_equipment": [],
                      "excluded": ["alpha", "beta", "gamma"]}, loop)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["equipment torch unavailable", "no substitution for gamma",
                              "substitution cycle involving alpha"]

    # 7. deterministic choice: lower priority wins, catalog order breaks ties
    ranked = [
        {"id": "worse", "for": "butter", "priority": 2, "result": {"name": "lard"}},
        {"id": "better", "for": "butter", "priority": 1, "result": {"name": "olive oil"}},
        {"id": "first-tie", "for": "cream", "priority": 1, "result": {"name": "oat cream"}},
        {"id": "later-tie", "for": "cream", "priority": 1, "result": {"name": "soy cream"}},
    ]
    out = adapt(recipe, {"target_yield": Fraction(3), "available_equipment": everything,
                         "excluded": ["butter", "cream"]}, ranked)
    assert out["possible"] and out["choices"] == ["better", "first-tie"]

    # 6. exact original print after adaptations; inputs never mutated
    assert recipe == frozen
    assert print_original(recipe) == "Herbed Butter Pasta\nServes 3\nMelt the butter in the pot.\n"

    print("all acceptance checks passed")
