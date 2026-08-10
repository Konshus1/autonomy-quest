"""recipebook.py

Adapt authored recipes to a target yield, available equipment, dietary
exclusions, and a substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is
JSON-shaped.  Catalog ``result`` keys understood here:

    name                    replacement ingredient name (required)
    quantity_factor         Fraction multiplier on the quantity
    unit                    replacement unit string
    preparation             replacement preparation string
    wording_changes         list of {"old": ..., "new": ...} literal edits
                            applied to instruction strings
    equipment_add           list of equipment names to add
    equipment_remove        list of equipment names to remove
    yield_factor            Fraction multiplier on the recipe yield; the
                            adapted recipe is rescaled back to the target
                            yield and a warning is recorded
    additional_ingredients  list of ingredient dicts, subject to the same
                            exclusions and catalog choices
"""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as authored, byte for byte."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    work = deepcopy(recipe)
    target = Fraction(request["target_yield"])
    available = set(request["available_equipment"])
    excluded = set(request["excluded"])
    scale = target / Fraction(work["yield"])

    by_name = {}
    for index, choice in enumerate(catalog):
        by_name.setdefault(choice["for"], []).append((choice["priority"], index, choice))
    for options in by_name.values():
        options.sort(key=lambda entry: (entry[0], entry[1]))

    state = {
        "instructions": list(work["instructions"]),
        "equipment": set(work["equipment"]),
        "yield_factor": Fraction(1),
        "choices": [],
        "warnings": [],
        "reasons": set(),
    }

    def resolve(ingredient, seen, st):
        """Substitute until the ingredient is not excluded.

        Returns the list of resolved additional ingredients introduced along
        the way, or None if the chain fails (the reason is recorded in st).
        The seen set is shared across the whole chain, including additional
        ingredients, so mutual reintroduction cannot loop forever.
        """
        extras = []
        while ingredient["name"] in excluded:
            name = ingredient["name"]
            if name in seen:
                st["reasons"].add("substitution cycle involving " + name)
                return None
            seen.add(name)
            options = by_name.get(name)
            if not options:
                st["reasons"].add("no substitution for " + name)
                return None
            choice = options[0][2]
            result = choice["result"]
            st["choices"].append(choice["id"])
            st["warnings"].append("substituted %s with %s" % (name, result["name"]))
            ingredient["name"] = result["name"]
            if "quantity_factor" in result:
                ingredient["quantity"] = ingredient["quantity"] * result["quantity_factor"]
            if "unit" in result:
                ingredient["unit"] = result["unit"]
            if "preparation" in result:
                ingredient["preparation"] = result["preparation"]
            for change in result.get("wording_changes", ()):
                st["instructions"] = [
                    line.replace(change["old"], change["new"]) for line in st["instructions"]
                ]
            for item in result.get("equipment_add", ()):
                st["equipment"].add(item)
            for item in result.get("equipment_remove", ()):
                st["equipment"].discard(item)
            if "yield_factor" in result:
                st["yield_factor"] = st["yield_factor"] * result["yield_factor"]
                st["warnings"].append("yield adjusted by substitution of " + name)
            for extra in result.get("additional_ingredients", ()):
                added = deepcopy(extra)
                added["quantity"] = Fraction(added["quantity"]) * scale
                sub_extras = resolve(added, seen, st)
                if sub_extras is None:
                    return None
                extras.append(added)
                extras.extend(sub_extras)
        return extras

    final_ingredients = []
    for ing in work["ingredients"]:
        item = dict(ing)
        item["quantity"] = Fraction(item["quantity"]) * scale
        extras = resolve(item, set(), state)
        final_ingredients.append(item)
        if extras:
            final_ingredients.extend(extras)

    adapted_components = []
    for comp in work.get("components", []):
        comp_ings = []
        for ing in comp.get("ingredients", []):
            item = dict(ing)
            item["quantity"] = Fraction(item["quantity"]) * scale
            comp_ings.append(item)
        if comp.get("optional"):
            trial = {
                "instructions": list(state["instructions"]),
                "equipment": set(state["equipment"]),
                "yield_factor": state["yield_factor"],
                "choices": [],
                "warnings": [],
                "reasons": set(),
            }
            resolved = []
            ok = True
            for item in comp_ings:
                extras = resolve(item, set(), trial)
                if extras is None:
                    ok = False
                    break
                resolved.append(item)
                resolved.extend(extras)
            if ok:
                state["instructions"] = trial["instructions"]
                state["equipment"] = trial["equipment"]
                state["yield_factor"] = trial["yield_factor"]
                state["choices"].extend(trial["choices"])
                state["warnings"].extend(trial["warnings"])
                comp_out = dict(comp)
                comp_out["ingredients"] = resolved
                adapted_components.append(comp_out)
            else:
                state["warnings"].append(
                    "omitted optional component " + str(comp.get("name", "unnamed"))
                )
        else:
            resolved = []
            for item in comp_ings:
                extras = resolve(item, set(), state)
                resolved.append(item)
                if extras:
                    resolved.extend(extras)
            comp_out = dict(comp)
            comp_out["ingredients"] = resolved
            adapted_components.append(comp_out)

    for item in sorted(state["equipment"]):
        if item not in available:
            state["reasons"].add("equipment %s unavailable" % item)

    if state["reasons"]:
        return {
            "possible": False,
            "recipe": None,
            "choices": state["choices"],
            "warnings": sorted(state["warnings"]),
            "reasons": sorted(state["reasons"]),
        }

    if state["yield_factor"] != 1:
        for item in final_ingredients:
            item["quantity"] = item["quantity"] / state["yield_factor"]
        for comp in adapted_components:
            for item in comp.get("ingredients", []):
                item["quantity"] = item["quantity"] / state["yield_factor"]

    adapted = {
        "title": work["title"],
        "yield": target,
        "ingredients": final_ingredients,
        "equipment": sorted(state["equipment"]),
        "instructions": state["instructions"],
        "components": adapted_components,
        "authored_text": work["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": state["choices"],
        "warnings": sorted(state["warnings"]),
        "reasons": [],
    }


if __name__ == "__main__":
    def sample():
        return {
            "title": "Herb Scones",
            "yield": Fraction(3),
            "ingredients": [
                {"id": "flour", "name": "flour", "quantity": Fraction(300),
                 "unit": "g", "preparation": "sifted"},
                {"id": "butter", "name": "butter", "quantity": Fraction(90),
                 "unit": "g", "preparation": "cubed"},
                {"id": "cream", "name": "cream", "quantity": Fraction(120),
                 "unit": "ml", "preparation": "chilled"},
            ],
            "equipment": ["oven", "whisk"],
            "instructions": [
                "Whip the cream until stiff.",
                "Rub the butter into the flour.",
            ],
            "components": [],
            "authored_text": "Herb Scones\nServes 3\n300 g flour, sifted\n"
                             "90 g butter, cubed\n120 ml cream, chilled\n"
                             "Whip the cream until stiff.\n"
                             "Rub the butter into the flour.\n",
        }

    base = sample()
    pristine = deepcopy(base)
    all_equipment = ["oven", "whisk"]

    # 1. Scaling 3 -> 5 servings with rational quantities.
    out = adapt(base, {"target_yield": Fraction(5),
                       "available_equipment": all_equipment, "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    q = {i["name"]: i["quantity"] for i in out["recipe"]["ingredients"]}
    assert q["flour"] == Fraction(500)
    assert q["butter"] == Fraction(150)
    assert q["cream"] == Fraction(200)
    assert out["recipe"]["yield"] == Fraction(5)

    # 2. Chained substitution: butter -> margarine -> olive oil.
    cat = [
        {"id": "b2m", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "m2o", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "quantity_factor": Fraction(3, 4),
                    "unit": "ml"}},
    ]
    out = adapt(base, {"target_yield": Fraction(3),
                       "available_equipment": all_equipment,
                       "excluded": ["butter", "margarine"]}, cat)
    assert out["possible"] and out["choices"] == ["b2m", "m2o"]
    oil = next(i for i in out["recipe"]["ingredients"] if i["name"] == "olive oil")
    assert oil["quantity"] == Fraction(90) * Fraction(3, 4)
    assert oil["unit"] == "ml"
    assert out["warnings"] == sorted(out["warnings"])

    # 3. Substitution changing a referenced verb and equipment.
    cat = [{"id": "c2cc", "for": "cream", "priority": 1, "result": {
        "name": "coconut cream",
        "preparation": "shaken",
        "wording_changes": [{"old": "Whip the cream",
                             "new": "Shake the coconut cream"}],
        "equipment_remove": ["whisk"],
        "equipment_add": ["jar"],
    }}]
    out = adapt(base, {"target_yield": Fraction(3),
                       "available_equipment": ["jar", "oven"],
                       "excluded": ["cream"]}, cat)
    assert out["possible"]
    assert out["recipe"]["instructions"][0] == "Shake the coconut cream until stiff."
    assert out["recipe"]["equipment"] == ["jar", "oven"]
    cc = next(i for i in out["recipe"]["ingredients"] if i["name"] == "coconut cream")
    assert cc["preparation"] == "shaken"

    # 4a. Exclusion reintroduced later by an additional ingredient: impossible.
    cat = [{"id": "c2sc", "for": "cream", "priority": 1, "result": {
        "name": "set custard",
        "additional_ingredients": [
            {"id": "gelatin", "name": "gelatin", "quantity": Fraction(5),
             "unit": "g", "preparation": "bloomed"}],
    }}]
    out = adapt(base, {"target_yield": Fraction(3),
                       "available_equipment": all_equipment,
                       "excluded": ["cream", "gelatin"]}, cat)
    assert not out["possible"] and out["recipe"] is None
    assert "no substitution for gelatin" in out["reasons"]
    assert out["choices"] == ["c2sc"]

    # 4b. Same reintroduction, resolved by a further chained choice.
    cat_fix = cat + [{"id": "g2a", "for": "gelatin", "priority": 1,
                      "result": {"name": "agar"}}]
    out = adapt(base, {"target_yield": Fraction(3),
                       "available_equipment": all_equipment,
                       "excluded": ["cream", "gelatin"]}, cat_fix)
    assert out["possible"] and out["choices"] == ["c2sc", "g2a"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "agar" in names and "gelatin" not in names and "cream" not in names

    # 5. Multiple simultaneous impossibility reasons, sorted.
    out = adapt(base, {"target_yield": Fraction(3),
                       "available_equipment": ["oven"],
                       "excluded": ["butter"]}, [])
    assert not out["possible"]
    assert out["reasons"] == ["equipment whisk unavailable",
                              "no substitution for butter"]

    # Cycle detection.
    cat = [
        {"id": "b2m", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "m2b", "for": "margarine", "priority": 1,
         "result": {"name": "butter"}},
    ]
    out = adapt(base, {"target_yield": Fraction(3),
                       "available_equipment": all_equipment,
                       "excluded": ["butter", "margarine"]}, cat)
    assert not out["possible"]
    assert "substitution cycle involving butter" in out["reasons"]

    # 6. Exact original print after adaptations, and no input mutation.
    assert print_original(base) == pristine["authored_text"]
    assert base == pristine

    # 7. Deterministic choice: lower priority wins; catalog order breaks ties.
    cat = [
        {"id": "worse", "for": "butter", "priority": 2,
         "result": {"name": "ghee"}},
        {"id": "best", "for": "butter", "priority": 1,
         "result": {"name": "olive oil"}},
    ]
    out = adapt(base, {"target_yield": Fraction(3),
                       "available_equipment": all_equipment,
                       "excluded": ["butter"]}, cat)
    assert out["possible"] and out["choices"] == ["best"]

    cat = [
        {"id": "first", "for": "butter", "priority": 1,
         "result": {"name": "olive oil"}},
        {"id": "second", "for": "butter", "priority": 1,
         "result": {"name": "ghee"}},
    ]
    out = adapt(base, {"target_yield": Fraction(3),
                       "available_equipment": all_equipment,
                       "excluded": ["butter"]}, cat)
    assert out["possible"] and out["choices"] == ["first"]
    assert any(i["name"] == "olive oil" for i in out["recipe"]["ingredients"])

    # Optional component that cannot be adapted is omitted with a warning.
    with_comp = sample()
    with_comp["components"] = [{
        "name": "garnish", "optional": True,
        "ingredients": [{"id": "chives", "name": "chives",
                         "quantity": Fraction(10), "unit": "g",
                         "preparation": "snipped"}],
    }]
    out = adapt(with_comp, {"target_yield": Fraction(3),
                            "available_equipment": all_equipment,
                            "excluded": ["chives"]}, [])
    assert out["possible"]
    assert out["recipe"]["components"] == []
    assert "omitted optional component garnish" in out["warnings"]

    print("all acceptance checks passed")
