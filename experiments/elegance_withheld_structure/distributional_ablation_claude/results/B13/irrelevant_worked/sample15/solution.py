"""recipebook.py

Adapt authored recipes to a target yield, available equipment, dietary
exclusions, and a substitution catalog.  Standard library only; quantities
and yields are fractions.Fraction.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Catalog ``result`` keys understood by this implementation:
    name                    replacement ingredient name (required)
    quantity_factor         Fraction-able multiplier on the replaced quantity
    unit, preparation       overrides for the replaced ingredient
    wording_changes         list of {"old": str, "new": str}; applied, in the
                            order recorded, to every instruction string
    equipment_add           list of equipment names the substitute requires
    equipment_remove        list of equipment names no longer needed
    yield_factor            Fraction-able multiplier on the final yield
    additional_ingredients  list of {name, quantity, unit?, preparation?, id?};
                            quantities are per the authored yield and are
                            scaled, and each entry is subject to the same
                            exclusions and catalog choices (chaining)

Neither public function mutates its arguments.
"""

from fractions import Fraction


def print_original(recipe):
    """Return the authored text exactly as written."""
    return recipe["authored_text"]


def _best_choice(name, catalog):
    """Lowest priority wins; catalog order breaks ties."""
    best = None
    for idx, entry in enumerate(catalog):
        if entry["for"] != name:
            continue
        key = (entry["priority"], idx)
        if best is None or key < best[0]:
            best = (key, entry)
    return best[1] if best is not None else None


def _slug(name):
    return name.lower().replace(" ", "_")


def _new_ctx(scale, excluded, catalog):
    return {
        "scale": scale,
        "excluded": excluded,
        "catalog": catalog,
        "choices": [],
        "warnings": [],
        "reasons": set(),
        "wording": [],
        "eq_add": [],
        "eq_remove": [],
        "yield_factors": [],
    }


def _merge_ctx(dst, src):
    dst["choices"].extend(src["choices"])
    dst["warnings"].extend(src["warnings"])
    dst["reasons"] |= src["reasons"]
    dst["wording"].extend(src["wording"])
    dst["eq_add"].extend(src["eq_add"])
    dst["eq_remove"].extend(src["eq_remove"])
    dst["yield_factors"].extend(src["yield_factors"])


def _resolve(ing, ctx, chain=frozenset()):
    """Resolve one already-scaled ingredient against the exclusions.

    Returns the list of ingredients it becomes, or None if resolution
    failed (reasons have been recorded).  The chain holds ingredient
    names already substituted away on this path, for cycle detection.
    """
    name = ing["name"]
    if name not in ctx["excluded"]:
        return [ing]
    chain = chain | {name}
    choice = _best_choice(name, ctx["catalog"])
    if choice is None:
        ctx["reasons"].add("no substitution for " + name)
        return None
    result = choice["result"]
    new_name = result["name"]
    ctx["choices"].append(choice["id"])
    ctx["warnings"].append("substituted %s with %s" % (name, new_name))
    for wc in result.get("wording_changes") or []:
        ctx["wording"].append((wc["old"], wc["new"]))
    ctx["eq_add"].extend(result.get("equipment_add") or [])
    ctx["eq_remove"].extend(result.get("equipment_remove") or [])
    if "yield_factor" in result:
        ctx["yield_factors"].append(Fraction(result["yield_factor"]))

    replacement = {
        "id": ing["id"],
        "name": new_name,
        "quantity": ing["quantity"] * Fraction(result.get("quantity_factor", 1)),
        "unit": result.get("unit", ing["unit"]),
        "preparation": result.get("preparation", ing["preparation"]),
    }

    ok = True
    out = []
    if new_name in ctx["excluded"] and new_name in chain:
        ctx["reasons"].add("substitution cycle involving " + new_name)
        ok = False
    else:
        resolved = _resolve(replacement, ctx, chain)
        if resolved is None:
            ok = False
        else:
            out.extend(resolved)

    for extra in result.get("additional_ingredients") or []:
        extra_ing = {
            "id": extra.get("id", _slug(extra["name"])),
            "name": extra["name"],
            "quantity": Fraction(extra["quantity"]) * ctx["scale"],
            "unit": extra.get("unit", ""),
            "preparation": extra.get("preparation", ""),
        }
        if extra_ing["name"] in ctx["excluded"] and extra_ing["name"] in chain:
            ctx["reasons"].add("substitution cycle involving " + extra_ing["name"])
            ok = False
            continue
        resolved = _resolve(extra_ing, ctx, chain)
        if resolved is None:
            ok = False
        else:
            out.extend(resolved)

    return out if ok else None


def _resolve_all(ingredients, ctx):
    """Resolve a list of authored ingredients; returns (ok, resolved list)."""
    ok = True
    out = []
    for ing in ingredients:
        scaled = dict(ing, quantity=Fraction(ing["quantity"]) * ctx["scale"])
        resolved = _resolve(scaled, ctx)
        if resolved is None:
            ok = False
        else:
            out.extend(resolved)
    return ok, out


def _reword(text, pairs):
    for old, new in pairs:
        text = text.replace(old, new)
    return text


def adapt(recipe, request, catalog):
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])

    ctx = _new_ctx(scale, excluded, catalog)
    possible, ingredients = _resolve_all(recipe["ingredients"], ctx)

    kept_components = []
    for comp in recipe.get("components") or []:
        sub = _new_ctx(scale, excluded, catalog)
        comp_ok, comp_ings = _resolve_all(comp.get("ingredients") or [], sub)
        comp_equipment = sorted(set(comp.get("equipment") or []))
        for eq in comp_equipment:
            if eq not in available:
                sub["reasons"].add("equipment %s unavailable" % eq)
                comp_ok = False
        if comp_ok:
            _merge_ctx(ctx, sub)
            kept_components.append((comp, comp_ings, comp_equipment))
        elif comp.get("optional"):
            ctx["warnings"].append("omitted optional component %s" % comp["name"])
        else:
            _merge_ctx(ctx, sub)
            possible = False

    equipment = (set(recipe["equipment"]) - set(ctx["eq_remove"])) | set(ctx["eq_add"])
    for eq in equipment:
        if eq not in available:
            ctx["reasons"].add("equipment %s unavailable" % eq)
            possible = False

    final_yield = target
    for factor in ctx["yield_factors"]:
        final_yield *= factor
    if final_yield != target:
        ctx["warnings"].append("yield changed to %s" % final_yield)

    if ctx["reasons"]:
        possible = False
    if not possible:
        return {
            "possible": False,
            "recipe": None,
            "choices": ctx["choices"],
            "warnings": sorted(ctx["warnings"]),
            "reasons": sorted(ctx["reasons"]),
        }

    pairs = ctx["wording"]
    components = []
    for comp, comp_ings, comp_equipment in kept_components:
        adapted_comp = {
            "name": comp["name"],
            "ingredients": comp_ings,
            "instructions": [_reword(s, pairs) for s in comp.get("instructions") or []],
            "equipment": comp_equipment,
        }
        if "optional" in comp:
            adapted_comp["optional"] = comp["optional"]
        components.append(adapted_comp)

    adapted = {
        "title": recipe["title"],
        "yield": final_yield,
        "ingredients": ingredients,
        "equipment": sorted(equipment),
        "instructions": [_reword(s, pairs) for s in recipe["instructions"]],
        "components": components,
        "authored_text": recipe["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": ctx["choices"],
        "warnings": sorted(ctx["warnings"]),
        "reasons": [],
    }


if __name__ == "__main__":
    import copy

    F = Fraction
    recipe = {
        "title": "Herbed Cream Soup",
        "yield": F(3),
        "ingredients": [
            {"id": "cream", "name": "cream", "quantity": F(1), "unit": "cup",
             "preparation": "whipped"},
            {"id": "flour", "name": "flour", "quantity": F(3, 4), "unit": "cup",
             "preparation": "sifted"},
        ],
        "equipment": ["whisk", "saucepan"],
        "instructions": [
            "whip the cream with the whisk",
            "sift the flour into the saucepan",
        ],
        "components": [],
        "authored_text": "Herbed Cream Soup\nServes 3.\n1 cup cream, whipped.\n"
                         "3/4 cup flour, sifted.\n",
    }
    snapshot = copy.deepcopy(recipe)
    catalog = [
        {"id": "c1", "for": "cream", "priority": 1, "result": {
            "name": "coconut milk",
            "preparation": "shaken",
            "wording_changes": [
                {"old": "whip", "new": "shake"},
                {"old": "cream", "new": "coconut milk"},
                {"old": "whisk", "new": "jar"},
            ],
            "equipment_add": ["jar"],
            "equipment_remove": ["whisk"],
        }},
        {"id": "c2", "for": "cream", "priority": 2, "result": {"name": "oat milk"}},
        {"id": "f1", "for": "flour", "priority": 1, "result": {
            "name": "almond flour", "quantity_factor": F(1, 2)}},
        {"id": "a1", "for": "almond flour", "priority": 1, "result": {"name": "rice flour"}},
        {"id": "cm1", "for": "coconut milk", "priority": 1, "result": {"name": "cashew cream"}},
    ]

    # 1. scaling 3 -> 5 servings
    r = adapt(recipe, {"target_yield": F(5),
                       "available_equipment": ["whisk", "saucepan"],
                       "excluded": []}, catalog)
    assert r["possible"] and r["recipe"]["yield"] == F(5)
    assert r["recipe"]["ingredients"][0]["quantity"] == F(5, 3)
    assert r["recipe"]["ingredients"][1]["quantity"] == F(5, 4)
    assert r["reasons"] == []

    # 2. chained substitution: flour -> almond flour -> rice flour
    r = adapt(recipe, {"target_yield": F(3),
                       "available_equipment": ["whisk", "saucepan"],
                       "excluded": ["flour", "almond flour"]}, catalog)
    assert r["possible"] and r["choices"] == ["f1", "a1"]
    names = [i["name"] for i in r["recipe"]["ingredients"]]
    assert "rice flour" in names and "flour" not in names and "almond flour" not in names

    # 3. substitution changing a referenced verb and equipment
    r = adapt(recipe, {"target_yield": F(3),
                       "available_equipment": ["jar", "saucepan"],
                       "excluded": ["cream"]}, catalog)
    assert r["possible"] and r["choices"] == ["c1"]
    assert r["recipe"]["equipment"] == ["jar", "saucepan"]
    assert r["recipe"]["instructions"][0] == "shake the coconut milk with the jar"

    # 4. exclusion reintroduced by a substitution is chased through the catalog
    r = adapt(recipe, {"target_yield": F(3),
                       "available_equipment": ["jar", "saucepan"],
                       "excluded": ["cream", "coconut milk"]}, catalog)
    assert r["possible"] and r["choices"] == ["c1", "cm1"]
    assert all(i["name"] not in ("cream", "coconut milk")
               for i in r["recipe"]["ingredients"])

    # 4b. a closed loop is reported as a cycle
    cyc = catalog + [{"id": "bad", "for": "cashew cream", "priority": 1,
                      "result": {"name": "cream"}}]
    r = adapt(recipe, {"target_yield": F(3),
                       "available_equipment": ["jar", "saucepan"],
                       "excluded": ["cream", "coconut milk", "cashew cream"]}, cyc)
    assert not r["possible"] and r["recipe"] is None
    assert "substitution cycle involving cream" in r["reasons"]

    # 5. multiple simultaneous impossibility reasons, sorted
    r = adapt(recipe, {"target_yield": F(3),
                       "available_equipment": ["saucepan"],
                       "excluded": ["flour"]}, [])
    assert not r["possible"]
    assert r["reasons"] == ["equipment whisk unavailable", "no substitution for flour"]

    # optional component that cannot be adapted is dropped with a warning
    comp_recipe = dict(recipe, components=[{
        "name": "garnish", "optional": True,
        "ingredients": [{"id": "nuts", "name": "peanuts", "quantity": F(1, 4),
                         "unit": "cup", "preparation": "crushed"}],
        "instructions": ["scatter the peanuts"],
        "equipment": [],
    }])
    r = adapt(comp_recipe, {"target_yield": F(3),
                            "available_equipment": ["whisk", "saucepan"],
                            "excluded": ["peanuts"]}, catalog)
    assert r["possible"] and r["recipe"]["components"] == []
    assert "omitted optional component garnish" in r["warnings"]

    # 6. exact original print after all adaptations; inputs unmutated
    assert recipe == snapshot
    assert print_original(recipe) == recipe["authored_text"]

    # 7. deterministic choice: c1 (priority 1) beat c2 in test 3; equal
    # priorities fall back to catalog order
    tie = [
        {"id": "t1", "for": "cream", "priority": 1, "result": {"name": "soy cream"}},
        {"id": "t2", "for": "cream", "priority": 1, "result": {"name": "oat cream"}},
    ]
    r = adapt(recipe, {"target_yield": F(3),
                       "available_equipment": ["whisk", "saucepan"],
                       "excluded": ["cream"]}, tie)
    assert r["possible"] and r["choices"] == ["t1"]
    assert r["recipe"]["ingredients"][0]["name"] == "soy cream"

    print("all checks passed")
