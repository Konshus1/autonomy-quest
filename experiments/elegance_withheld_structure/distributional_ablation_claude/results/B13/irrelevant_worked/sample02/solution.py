"""recipebook: adapt recipes to a target yield, available equipment, and
dietary exclusions using a substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Catalog `result` keys understood here: name, quantity_factor, unit,
preparation, yield_factor, wording_changes ([{old,new}]), equipment_add,
equipment_remove, additional_ingredients.
Neither public function mutates its inputs.
"""

from fractions import Fraction


def print_original(recipe):
    """Return the authored text byte-for-byte."""
    return recipe["authored_text"]


def _copy_ingredient(ing):
    return {
        "id": ing.get("id"),
        "name": ing["name"],
        "quantity": ing["quantity"],
        "unit": ing.get("unit"),
        "preparation": ing.get("preparation"),
    }


def _best_choice(name, catalog):
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


class _Resolution:
    """Accumulates everything decided or diagnosed while adapting."""

    def __init__(self):
        self.choices = []            # catalog ids in application order
        self.reasons = set()         # every independently applicable reason
        self.warnings = set()
        self.wording_changes = []    # (old, new) in application order
        self.equipment_add = set()
        self.equipment_remove = set()
        self.yield_factor = Fraction(1)


def _resolve_ingredient(ing, excluded, catalog, res, chain):
    """Resolve one ingredient against exclusions, chaining substitutions.

    `chain` holds the excluded names already being substituted on this path,
    so a reintroduced ancestor is reported as a cycle instead of recursing
    forever.  Returns the list of final ingredients, or None on failure
    (with reasons recorded in `res`).
    """
    name = ing["name"]
    if name not in excluded:
        return [ing]
    if chain:
        res.warnings.add("excluded ingredient %s reintroduced" % name)
    if name in chain:
        res.reasons.add("substitution cycle involving %s" % name)
        return None
    choice = _best_choice(name, catalog)
    if choice is None:
        res.reasons.add("no substitution for %s" % name)
        return None
    res.choices.append(choice["id"])
    result = choice["result"]
    replacement = {
        "id": ing.get("id"),
        "name": result["name"],
        "quantity": ing["quantity"] * result.get("quantity_factor", Fraction(1)),
        "unit": result.get("unit", ing.get("unit")),
        "preparation": result.get("preparation", ing.get("preparation")),
    }
    for pair in result.get("wording_changes", []):
        res.wording_changes.append((pair["old"], pair["new"]))
    for item in result.get("equipment_add", []):
        res.equipment_add.add(item)
        res.warnings.add("substitution %s adds equipment %s" % (choice["id"], item))
    for item in result.get("equipment_remove", []):
        res.equipment_remove.add(item)
        res.warnings.add("substitution %s removes equipment %s" % (choice["id"], item))
    if "yield_factor" in result:
        res.yield_factor *= result["yield_factor"]
        res.warnings.add("substitution %s changes yield" % choice["id"])

    deeper_chain = chain + [name]
    ok = True
    out = []
    resolved = _resolve_ingredient(replacement, excluded, catalog, res, deeper_chain)
    if resolved is None:
        ok = False
    else:
        out.extend(resolved)
    for extra in result.get("additional_ingredients", []):
        extra_ing = {
            "id": extra.get("id", extra["name"].replace(" ", "_")),
            "name": extra["name"],
            "quantity": extra["quantity"],
            "unit": extra.get("unit"),
            "preparation": extra.get("preparation"),
        }
        resolved_extra = _resolve_ingredient(extra_ing, excluded, catalog, res,
                                             deeper_chain)
        if resolved_extra is None:
            ok = False
        else:
            out.extend(resolved_extra)
    return out if ok else None


def _apply_wording(instructions, changes):
    out = []
    for text in instructions:
        for old, new in changes:
            text = text.replace(old, new)
        out.append(text)
    return out


def adapt(recipe, request, catalog):
    res = _Resolution()
    excluded = set(request.get("excluded", []))
    available = set(request.get("available_equipment", []))

    failed = False
    ingredients = []
    for ing in recipe["ingredients"]:
        resolved = _resolve_ingredient(_copy_ingredient(ing), excluded, catalog,
                                       res, [])
        if resolved is None:
            failed = True
        else:
            ingredients.extend(resolved)

    components = []
    for comp in recipe.get("components", []):
        new_comp = dict(comp)
        comp_ings = []
        for ing in comp.get("ingredients", []):
            resolved = _resolve_ingredient(_copy_ingredient(ing), excluded,
                                           catalog, res, [])
            if resolved is None:
                failed = True
            else:
                comp_ings.extend(resolved)
        new_comp["ingredients"] = comp_ings
        components.append(new_comp)

    equipment = set(recipe.get("equipment", []))
    equipment -= res.equipment_remove
    equipment |= res.equipment_add
    needed = set(equipment)
    for comp in recipe.get("components", []):
        needed |= set(comp.get("equipment", []))
    for item in needed - available:
        res.reasons.add("equipment %s unavailable" % item)

    if failed or res.reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": list(res.choices),
            "warnings": sorted(res.warnings),
            "reasons": sorted(res.reasons),
        }

    target_yield = request["target_yield"]
    effective_yield = recipe["yield"] * res.yield_factor
    scale = Fraction(target_yield) / effective_yield
    for ing in ingredients:
        ing["quantity"] = ing["quantity"] * scale

    instructions = _apply_wording(recipe.get("instructions", []),
                                  res.wording_changes)
    for comp in components:
        for ing in comp["ingredients"]:
            ing["quantity"] = ing["quantity"] * scale
        if "instructions" in comp:
            comp["instructions"] = _apply_wording(comp["instructions"],
                                                  res.wording_changes)

    for old, new in res.wording_changes:
        if not any(new in text for text in instructions):
            res.warnings.add("wording change not applied: %s" % old)

    adapted = {
        "title": recipe["title"],
        "yield": target_yield,
        "ingredients": ingredients,
        "equipment": sorted(equipment),
        "instructions": instructions,
        "components": components,
        "authored_text": recipe["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": list(res.choices),
        "warnings": sorted(res.warnings),
        "reasons": [],
    }


if __name__ == "__main__":
    import copy

    F = Fraction

    def base_recipe():
        return {
            "title": "Honey Butter Cake",
            "yield": F(3),
            "ingredients": [
                {"id": "i1", "name": "flour", "quantity": F(2), "unit": "cup",
                 "preparation": "sifted"},
                {"id": "i2", "name": "butter", "quantity": F(1, 2),
                 "unit": "cup", "preparation": "softened"},
                {"id": "i3", "name": "honey", "quantity": F(3), "unit": "tbsp",
                 "preparation": None},
            ],
            "equipment": ["oven", "stand mixer"],
            "instructions": [
                "Cream the butter until fluffy.",
                "Fold in the flour and honey.",
                "Bake in the oven.",
            ],
            "components": [],
            "authored_text": "Honey Butter Cake\nServes 3.\nCream the butter until fluffy.\n",
        }

    everything = ["oven", "stand mixer", "whisk", "saucepan"]

    # 1. Scaling 3 -> 5 servings.
    r = base_recipe()
    out = adapt(r, {"target_yield": F(5), "available_equipment": everything,
                    "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == F(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == F(2) * F(5, 3)
    assert out["recipe"]["ingredients"][1]["quantity"] == F(1, 2) * F(5, 3)

    # 2. Chained substitution: butter -> margarine (also excluded) -> oil.
    catalog = [
        {"id": "s_butter", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "s_marg", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "quantity_factor": F(3, 4),
                    "unit": "cup", "preparation": None}},
    ]
    out = adapt(base_recipe(),
                {"target_yield": F(3), "available_equipment": everything,
                 "excluded": ["butter", "margarine"]}, catalog)
    assert out["possible"]
    assert out["choices"] == ["s_butter", "s_marg"]
    fat = [i for i in out["recipe"]["ingredients"] if i["name"] == "olive oil"]
    assert fat and fat[0]["quantity"] == F(1, 2) * F(3, 4)

    # 3. Substitution changing a referenced verb and equipment.
    catalog = [
        {"id": "s_verb", "for": "butter", "priority": 1,
         "result": {"name": "coconut oil",
                    "wording_changes": [{"old": "Cream", "new": "Whisk"}],
                    "equipment_remove": ["stand mixer"],
                    "equipment_add": ["whisk"]}},
    ]
    out = adapt(base_recipe(),
                {"target_yield": F(3), "available_equipment": everything,
                 "excluded": ["butter"]}, catalog)
    assert out["possible"]
    assert out["recipe"]["instructions"][0] == "Whisk the butter until fluffy."
    assert "whisk" in out["recipe"]["equipment"]
    assert "stand mixer" not in out["recipe"]["equipment"]
    assert out["recipe"]["equipment"] == sorted(out["recipe"]["equipment"])

    # 4. Exclusion reintroduced later by an additional ingredient, then chained.
    catalog = [
        {"id": "s_honey", "for": "honey", "priority": 1,
         "result": {"name": "maple syrup",
                    "additional_ingredients": [
                        {"name": "sugar", "quantity": F(1), "unit": "tbsp"}]}},
        {"id": "s_sugar", "for": "sugar", "priority": 1,
         "result": {"name": "stevia", "quantity_factor": F(1, 8)}},
    ]
    out = adapt(base_recipe(),
                {"target_yield": F(3), "available_equipment": everything,
                 "excluded": ["honey", "sugar"]}, catalog)
    assert out["possible"]
    assert out["choices"] == ["s_honey", "s_sugar"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "stevia" in names and "sugar" not in names and "honey" not in names
    assert "excluded ingredient sugar reintroduced" in out["warnings"]

    # 4b. Substitution cycle reason text.
    catalog = [
        {"id": "c1", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "c2", "for": "margarine", "priority": 1,
         "result": {"name": "butter"}},
    ]
    out = adapt(base_recipe(),
                {"target_yield": F(3), "available_equipment": everything,
                 "excluded": ["butter", "margarine"]}, catalog)
    assert not out["possible"] and out["recipe"] is None
    assert "substitution cycle involving butter" in out["reasons"]

    # 5. Multiple simultaneous impossibility reasons, sorted.
    out = adapt(base_recipe(),
                {"target_yield": F(5), "available_equipment": ["whisk"],
                 "excluded": ["butter", "honey"]}, [])
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == sorted([
        "no substitution for butter",
        "no substitution for honey",
        "equipment oven unavailable",
        "equipment stand mixer unavailable",
    ])

    # 6. Exact original print after adaptations; inputs never mutated.
    r = base_recipe()
    snapshot = copy.deepcopy(r)
    req = {"target_yield": F(5), "available_equipment": everything,
           "excluded": ["butter"]}
    cat = [{"id": "s1", "for": "butter", "priority": 1,
            "result": {"name": "coconut oil"}}]
    req_snap, cat_snap = copy.deepcopy(req), copy.deepcopy(cat)
    adapt(r, req, cat)
    assert r == snapshot and req == req_snap and cat == cat_snap
    assert print_original(r) == snapshot["authored_text"]
    assert print_original(r) is r["authored_text"]

    # 7. Deterministic choice: priority wins, then catalog order.
    catalog = [
        {"id": "later_pri", "for": "butter", "priority": 2,
         "result": {"name": "shortening"}},
        {"id": "best_pri", "for": "butter", "priority": 1,
         "result": {"name": "coconut oil"}},
        {"id": "tied_pri", "for": "butter", "priority": 1,
         "result": {"name": "olive oil"}},
    ]
    out = adapt(base_recipe(),
                {"target_yield": F(3), "available_equipment": everything,
                 "excluded": ["butter"]}, catalog)
    assert out["choices"] == ["best_pri"]
    assert any(i["name"] == "coconut oil" for i in out["recipe"]["ingredients"])

    # Yield-affecting substitution still lands exactly on the target yield.
    catalog = [
        {"id": "s_y", "for": "honey", "priority": 1,
         "result": {"name": "agave", "yield_factor": F(4, 3)}},
    ]
    out = adapt(base_recipe(),
                {"target_yield": F(5), "available_equipment": everything,
                 "excluded": ["honey"]}, catalog)
    assert out["possible"] and out["recipe"]["yield"] == F(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == F(2) * F(5) / F(4)
    assert "substitution s_y changes yield" in out["warnings"]
    assert out["warnings"] == sorted(out["warnings"])

    print("all acceptance checks passed")
