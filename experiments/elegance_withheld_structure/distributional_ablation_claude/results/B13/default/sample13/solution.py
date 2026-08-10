"""Recipe adaptation with rational quantities, substitutions, and exclusions.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; all other values are
JSON-shaped.  A catalog choice is {id, for, priority, result} where
``result`` may hold: name, quantity_factor, unit, preparation,
wording_changes ([{old, new}]), equipment_add, equipment_remove, and
additional (a list of ingredients that join the recipe and are subject
to the same exclusions and catalog choices).
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe's authored text byte-for-byte."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    work = copy.deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    available = set(request.get("available_equipment") or [])
    excluded = set(request.get("excluded") or [])
    scale = target_yield / Fraction(work["yield"])

    ranked = {}
    for index, entry in enumerate(catalog):
        ranked.setdefault(entry["for"], []).append((entry["priority"], index, entry))
    for options in ranked.values():
        options.sort(key=lambda item: (item[0], item[1]))

    choices = []
    warnings = []
    reasons = set()
    wording = []
    equipment = list(work.get("equipment") or [])

    def resolve(ingredient, lineage):
        ingredient = dict(ingredient)
        if ingredient.get("quantity") is not None:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale
        chain = set(lineage)
        extras = []
        while ingredient["name"] in excluded:
            name = ingredient["name"]
            if name in chain:
                reasons.add("substitution cycle involving %s" % name)
                return []
            chain.add(name)
            options = ranked.get(name)
            if not options:
                reasons.add("no substitution for %s" % name)
                return []
            entry = options[0][2]
            result = entry["result"]
            choices.append(entry["id"])
            ingredient["name"] = result["name"]
            if "quantity_factor" in result and ingredient.get("quantity") is not None:
                ingredient["quantity"] *= Fraction(result["quantity_factor"])
            if "unit" in result:
                ingredient["unit"] = result["unit"]
            if "preparation" in result:
                ingredient["preparation"] = result["preparation"]
            wording.extend(result.get("wording_changes") or [])
            for item in result.get("equipment_add") or []:
                if item not in equipment:
                    equipment.append(item)
            for item in result.get("equipment_remove") or []:
                if item in equipment:
                    equipment.remove(item)
            extras.extend(result.get("additional") or [])
            warnings.append("substituted %s for %s" % (ingredient["name"], name))
        resolved = [ingredient]
        for extra in extras:
            resolved.extend(resolve(extra, chain))
        return resolved

    ingredients = []
    for ingredient in work["ingredients"]:
        ingredients.extend(resolve(ingredient, frozenset()))

    components = []
    for component in work.get("components") or []:
        component = dict(component)
        if isinstance(component.get("ingredients"), list):
            gathered = []
            for ingredient in component["ingredients"]:
                gathered.extend(resolve(ingredient, frozenset()))
            component["ingredients"] = gathered
        components.append(component)

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

    def reword(lines):
        edited = []
        for line in lines:
            for change in wording:
                line = line.replace(change["old"], change["new"])
            edited.append(line)
        return edited

    work["yield"] = target_yield
    work["ingredients"] = ingredients
    work["equipment"] = sorted(equipment)
    work["instructions"] = reword(work.get("instructions") or [])
    for component in components:
        if isinstance(component.get("instructions"), list):
            component["instructions"] = reword(component["instructions"])
    work["components"] = components

    return {
        "possible": True,
        "recipe": work,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }


if __name__ == "__main__":
    F = Fraction

    def base_recipe():
        return {
            "title": "Pancakes",
            "yield": F(3),
            "ingredients": [
                {"id": "flour", "name": "flour", "quantity": F(300),
                 "unit": "g", "preparation": "sifted"},
                {"id": "butter", "name": "butter", "quantity": F(60),
                 "unit": "g", "preparation": "melted"},
                {"id": "milk", "name": "milk", "quantity": F(3, 2),
                 "unit": "cup", "preparation": ""},
            ],
            "equipment": ["bowl", "whisk"],
            "instructions": [
                "Whisk the milk into the flour.",
                "Fold in the melted butter.",
            ],
            "components": [],
            "authored_text": ("Pancakes\nServes 3.\n300 g flour, sifted\n"
                              "60 g butter, melted\n1 1/2 cup milk\n"
                              "Whisk the milk into the flour.\n"
                              "Fold in the melted butter.\n"),
        }

    everything = ["blender", "bowl", "saucepan", "whisk"]

    # 1. Scaling 3 -> 5 servings.
    out = adapt(base_recipe(),
                {"target_yield": F(5), "available_equipment": everything,
                 "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == F(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == F(500)
    assert out["recipe"]["ingredients"][2]["quantity"] == F(5, 2)

    # 2. Chained substitution: butter -> margarine -> oil.
    catalog = [
        {"id": "b-marg", "for": "butter", "priority": 1,
         "result": {"name": "margarine", "quantity_factor": F(1)}},
        {"id": "m-oil", "for": "margarine", "priority": 1,
         "result": {"name": "oil", "quantity_factor": F(3, 4),
                    "unit": "ml", "preparation": ""}},
    ]
    out = adapt(base_recipe(),
                {"target_yield": F(3), "available_equipment": everything,
                 "excluded": ["butter", "margarine"]}, catalog)
    assert out["possible"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "oil" in names and "butter" not in names and "margarine" not in names
    assert out["choices"] == ["b-marg", "m-oil"]
    oil = out["recipe"]["ingredients"][1]
    assert oil["quantity"] == F(45) and oil["unit"] == "ml"

    # 3. Substitution changing a referenced verb and equipment.
    catalog = [
        {"id": "milk-oat", "for": "milk", "priority": 1,
         "result": {"name": "oat milk",
                    "wording_changes": [{"old": "Whisk the milk",
                                         "new": "Blend the oat milk"}],
                    "equipment_add": ["blender"],
                    "equipment_remove": ["whisk"]}},
    ]
    out = adapt(base_recipe(),
                {"target_yield": F(3), "available_equipment": everything,
                 "excluded": ["milk"]}, catalog)
    assert out["possible"]
    assert out["recipe"]["instructions"][0] == "Blend the oat milk into the flour."
    assert out["recipe"]["equipment"] == ["blender", "bowl"]

    # 4. Exclusion reintroduced later by an additional ingredient.
    def butterless():
        r = base_recipe()
        r["ingredients"] = [i for i in r["ingredients"] if i["name"] != "butter"]
        r["instructions"] = ["Whisk the milk into the flour."]
        return r

    cashew = {"id": "milk-cashew", "for": "milk", "priority": 1,
              "result": {"name": "cashew cream",
                         "additional": [{"id": "extra-butter", "name": "butter",
                                         "quantity": F(10), "unit": "g",
                                         "preparation": ""}]}}
    out = adapt(butterless(),
                {"target_yield": F(3), "available_equipment": everything,
                 "excluded": ["milk", "butter"]}, [cashew])
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["no substitution for butter"]

    # 4b. The reintroduced exclusion resolves through a further chained choice.
    out = adapt(butterless(),
                {"target_yield": F(3), "available_equipment": everything,
                 "excluded": ["milk", "butter"]},
                [cashew, {"id": "b-oil", "for": "butter", "priority": 1,
                          "result": {"name": "oil"}}])
    assert out["possible"]
    assert out["choices"] == ["milk-cashew", "b-oil"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "oil" in names and "butter" not in names

    # 4c. Substitution cycle.
    catalog = [
        {"id": "b-ghee", "for": "butter", "priority": 1, "result": {"name": "ghee"}},
        {"id": "g-butter", "for": "ghee", "priority": 1, "result": {"name": "butter"}},
    ]
    out = adapt(base_recipe(),
                {"target_yield": F(3), "available_equipment": everything,
                 "excluded": ["butter", "ghee"]}, catalog)
    assert not out["possible"]
    assert out["reasons"] == ["substitution cycle involving butter"]

    # 5. Multiple simultaneous impossibility reasons.
    out = adapt(base_recipe(),
                {"target_yield": F(4), "available_equipment": ["bowl"],
                 "excluded": ["flour"]}, [])
    assert not out["possible"]
    assert out["reasons"] == ["equipment whisk unavailable",
                              "no substitution for flour"]

    # 6. Exact original print after adaptations; inputs unmutated.
    original = base_recipe()
    snapshot = copy.deepcopy(original)
    adapt(original,
          {"target_yield": F(7), "available_equipment": everything,
           "excluded": ["butter"]},
          [{"id": "b-marg", "for": "butter", "priority": 1,
            "result": {"name": "margarine"}}])
    assert original == snapshot
    assert print_original(original) == snapshot["authored_text"]

    # 7. Deterministic choice by explicit priority, catalog order breaking ties.
    catalog = [
        {"id": "late-low", "for": "butter", "priority": 2,
         "result": {"name": "shortening"}},
        {"id": "best", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "tied", "for": "butter", "priority": 1,
         "result": {"name": "oil"}},
    ]
    out = adapt(base_recipe(),
                {"target_yield": F(3), "available_equipment": everything,
                 "excluded": ["butter"]}, catalog)
    assert out["possible"]
    assert out["choices"] == ["best"]
    assert [i["name"] for i in out["recipe"]["ingredients"]][1] == "margarine"

    print("all acceptance checks passed")
