"""recipebook.py -- adapt authored recipes to a target yield, available
equipment, dietary exclusions, and a substitution catalog.

Data shapes (all quantities and yields are fractions.Fraction; everything
else is JSON-shaped):

recipe     = {title, yield, ingredients, equipment, instructions,
              components, authored_text}
ingredient = {id, name, quantity, unit, preparation}
component  = {name, ingredients?, instructions?}          # optional part
request    = {target_yield, available_equipment, excluded}
choice     = {id, for, priority, result}
result     = {name,
              quantity_factor?,          # Fraction multiplier
              unit?, preparation?,       # replacement strings
              yield_factor?,             # Fraction; substitution alters yield
              wording_changes?,          # [{old, new}] applied to instructions
              equipment_add?, equipment_remove?,   # lists of names
              additional_ingredients?}   # ingredient dicts, per original yield

adapt() returns {possible, recipe, choices, warnings, reasons}.
Neither adapt() nor print_original() mutates its inputs.
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the authored text exactly as written."""
    return recipe["authored_text"]


def _best_choice(catalog, name):
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


def adapt(recipe, request, catalog):
    adapted = copy.deepcopy(recipe)
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / Fraction(recipe["yield"])
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])

    reasons = set()
    warnings = []
    choices = []
    equipment = list(adapted.get("equipment", []))
    wording_edits = []
    yield_factor = Fraction(1)

    groups = [adapted["ingredients"]]
    for component in adapted.get("components", []):
        if component.get("ingredients"):
            groups.append(component["ingredients"])

    for group in groups:
        for ingredient in group:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    for group in groups:
        i = 0
        guard = 0
        limit = 1000 * (len(catalog) + len(group) + 1)
        while i < len(group):
            guard += 1
            if guard > limit:
                reasons.add("substitution cycle involving %s" % group[i]["name"])
                break
            ingredient = group[i]
            seen = set()
            while ingredient["name"] in excluded:
                name = ingredient["name"]
                if name in seen:
                    reasons.add("substitution cycle involving %s" % name)
                    break
                seen.add(name)
                choice = _best_choice(catalog, name)
                if choice is None:
                    reasons.add("no substitution for %s" % name)
                    break
                choices.append(choice["id"])
                result = choice["result"]
                ingredient["name"] = result["name"]
                if "quantity_factor" in result:
                    ingredient["quantity"] *= Fraction(result["quantity_factor"])
                if "unit" in result:
                    ingredient["unit"] = result["unit"]
                if "preparation" in result:
                    ingredient["preparation"] = result["preparation"]
                for change in result.get("wording_changes", []):
                    wording_edits.append((change["old"], change["new"]))
                for item in result.get("equipment_add", []):
                    if item not in equipment:
                        equipment.append(item)
                for item in result.get("equipment_remove", []):
                    if item in equipment:
                        equipment.remove(item)
                if "yield_factor" in result:
                    yield_factor *= Fraction(result["yield_factor"])
                    warnings.append(
                        "yield adjusted by substitution %s" % choice["id"])
                insert_at = i + 1
                for extra in result.get("additional_ingredients", []):
                    added = copy.deepcopy(extra)
                    added["quantity"] = Fraction(added["quantity"]) * scale
                    group.insert(insert_at, added)
                    insert_at += 1
                if ingredient["name"] in excluded:
                    warnings.append(
                        "substitution %s introduces excluded ingredient %s"
                        % (choice["id"], ingredient["name"]))
            i += 1

    equipment = sorted(equipment)
    for item in equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)

    warnings = sorted(warnings)
    if reasons:
        return {"possible": False, "recipe": None, "choices": choices,
                "warnings": warnings, "reasons": sorted(reasons)}

    if yield_factor != 1:
        for group in groups:
            for ingredient in group:
                ingredient["quantity"] /= yield_factor

    def edit(text):
        for old, new in wording_edits:
            text = text.replace(old, new)
        return text

    adapted["instructions"] = [edit(t) for t in adapted["instructions"]]
    for component in adapted.get("components", []):
        if component.get("instructions"):
            component["instructions"] = [edit(t) for t in component["instructions"]]

    adapted["yield"] = target_yield
    adapted["equipment"] = equipment
    return {"possible": True, "recipe": adapted, "choices": choices,
            "warnings": warnings, "reasons": []}


if __name__ == "__main__":
    F = Fraction

    def make_recipe():
        return {
            "title": "Pound Cake",
            "yield": F(3),
            "ingredients": [
                {"id": "i1", "name": "butter", "quantity": F(3, 2),
                 "unit": "cup", "preparation": "softened"},
                {"id": "i2", "name": "flour", "quantity": F(3),
                 "unit": "cup", "preparation": "sifted"},
            ],
            "equipment": ["mixer", "loaf pan"],
            "instructions": ["Cream the butter until fluffy.",
                             "Fold in the flour."],
            "components": [
                {"name": "glaze",
                 "ingredients": [
                     {"id": "c1", "name": "milk", "quantity": F(1, 4),
                      "unit": "cup", "preparation": "warmed"}],
                 "instructions": ["Drizzle the glaze."]},
            ],
            "authored_text": ("Pound Cake\nServes 3.\n"
                              "Cream the butter until fluffy.\n"
                              "Fold in the flour.\n"),
        }

    # 1. Scaling 3 -> 5 servings, components included.
    out = adapt(make_recipe(),
                {"target_yield": F(5),
                 "available_equipment": ["mixer", "loaf pan"],
                 "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == F(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == F(5, 2)
    assert out["recipe"]["components"][0]["ingredients"][0]["quantity"] == F(5, 12)

    # 2 + 4. Chained substitution; exclusion reintroduced mid-chain is
    # detected, warned about, and resolved by a further substitution.
    chain_catalog = [
        {"id": "sub-butter-marg", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "sub-marg-oil", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "quantity_factor": F(3, 4),
                    "unit": "cup", "preparation": "room temperature"}},
    ]
    out = adapt(make_recipe(),
                {"target_yield": F(3),
                 "available_equipment": ["mixer", "loaf pan"],
                 "excluded": ["butter", "margarine"]}, chain_catalog)
    assert out["possible"]
    assert out["choices"] == ["sub-butter-marg", "sub-marg-oil"]
    assert out["recipe"]["ingredients"][0]["name"] == "olive oil"
    assert out["recipe"]["ingredients"][0]["quantity"] == F(9, 8)
    assert out["warnings"] == [
        "substitution sub-butter-marg introduces excluded ingredient margarine"]

    # 3. Substitution changing a referenced verb and the equipment set.
    verb_catalog = [
        {"id": "sub-butter-oil", "for": "butter", "priority": 1,
         "result": {"name": "olive oil",
                    "wording_changes": [{"old": "Cream the butter",
                                         "new": "Whisk the olive oil"}],
                    "equipment_remove": ["mixer"],
                    "equipment_add": ["whisk"]}},
    ]
    out = adapt(make_recipe(),
                {"target_yield": F(3),
                 "available_equipment": ["whisk", "loaf pan"],
                 "excluded": ["butter"]}, verb_catalog)
    assert out["possible"]
    assert out["recipe"]["instructions"][0] == "Whisk the olive oil until fluffy."
    assert out["recipe"]["equipment"] == ["loaf pan", "whisk"]

    # 5. Multiple simultaneous impossibility reasons, sorted.
    out = adapt(make_recipe(),
                {"target_yield": F(3),
                 "available_equipment": ["loaf pan"],
                 "excluded": ["milk"]}, [])
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["equipment mixer unavailable",
                              "no substitution for milk"]

    # Substitution cycle detection.
    cycle_catalog = [
        {"id": "a", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "b", "for": "margarine", "priority": 1,
         "result": {"name": "butter"}},
    ]
    out = adapt(make_recipe(),
                {"target_yield": F(3),
                 "available_equipment": ["mixer", "loaf pan"],
                 "excluded": ["butter", "margarine"]}, cycle_catalog)
    assert not out["possible"]
    assert out["reasons"] == ["substitution cycle involving butter"]

    # 6. Original prints byte-for-byte after adaptations; no mutation.
    original = make_recipe()
    snapshot = copy.deepcopy(original)
    adapt(original,
          {"target_yield": F(7),
           "available_equipment": ["mixer", "loaf pan"],
           "excluded": ["butter"]}, verb_catalog)
    assert original == snapshot
    assert print_original(original) == snapshot["authored_text"]

    # 7. Deterministic priority: lower number wins, catalog order breaks ties.
    priority_catalog = [
        {"id": "late-low", "for": "butter", "priority": 2,
         "result": {"name": "lard"}},
        {"id": "best", "for": "butter", "priority": 1,
         "result": {"name": "olive oil"}},
        {"id": "tie", "for": "butter", "priority": 1,
         "result": {"name": "ghee"}},
    ]
    out = adapt(make_recipe(),
                {"target_yield": F(3),
                 "available_equipment": ["mixer", "loaf pan"],
                 "excluded": ["butter"]}, priority_catalog)
    assert out["choices"] == ["best"]
    assert out["recipe"]["ingredients"][0]["name"] == "olive oil"

    print("all acceptance checks passed")
