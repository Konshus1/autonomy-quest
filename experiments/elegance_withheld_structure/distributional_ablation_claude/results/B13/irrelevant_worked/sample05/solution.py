"""recipebook.py

Adapt authored recipes to a target yield, available equipment, dietary
exclusions, and a substitution catalog, while keeping the authored
original printable byte-for-byte.

Only the Python standard library is used; quantities and yields are
fractions.Fraction throughout.
"""

from fractions import Fraction

__all__ = ["adapt", "print_original"]


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


def _resolve_ingredient(ingredient, excluded, catalog, chain, out):
    """Resolve one ingredient (and any ingredients its substitutions add)
    into out; returns False if any needed substitution is unavailable or
    cyclic, recording the reason."""
    current = {
        "id": ingredient.get("id", ingredient["name"]),
        "name": ingredient["name"],
        "quantity": Fraction(ingredient["quantity"]),
        "unit": ingredient.get("unit"),
        "preparation": ingredient.get("preparation"),
    }
    visited = set(chain)
    additions = []
    ok = True
    while current["name"] in excluded:
        if current["name"] in visited:
            out["reasons"].add("substitution cycle involving %s" % current["name"])
            ok = False
            break
        visited.add(current["name"])
        choice = _best_choice(current["name"], catalog)
        if choice is None:
            out["reasons"].add("no substitution for %s" % current["name"])
            ok = False
            break
        out["choices"].append(choice["id"])
        result = choice["result"]
        current = {
            "id": current["id"],
            "name": result["name"],
            "quantity": current["quantity"] * Fraction(result.get("quantity_factor", 1)),
            "unit": result.get("unit", current["unit"]),
            "preparation": result.get("preparation", current["preparation"]),
        }
        out["wording"].extend(result.get("wording_changes", []))
        out["eq_add"].extend(result.get("equipment_add", []))
        out["eq_remove"].extend(result.get("equipment_remove", []))
        additions.extend(result.get("additional_ingredients", []))
    if ok:
        out["ingredients"].append(current)
    # Added ingredients face the same exclusions and catalog; they inherit
    # the visited chain so a reintroduction of something already
    # substituted away is reported as a cycle rather than looping.
    for extra in additions:
        ok = _resolve_ingredient(extra, excluded, catalog, frozenset(visited), out) and ok
    return ok


def _resolve_section(ingredients, excluded, catalog):
    out = {
        "ingredients": [],
        "reasons": set(),
        "choices": [],
        "wording": [],
        "eq_add": [],
        "eq_remove": [],
    }
    for ingredient in ingredients:
        _resolve_ingredient(ingredient, excluded, catalog, frozenset(), out)
    return out


def _scaled(ingredients, scale):
    return [dict(i, quantity=i["quantity"] * scale) for i in ingredients]


def _reworded(instructions, pairs):
    result = []
    for text in instructions:
        for pair in pairs:
            text = text.replace(pair["old"], pair["new"])
        result.append(text)
    return result


def adapt(recipe, request, catalog):
    excluded = set(request.get("excluded") or [])
    available = set(request.get("available_equipment") or [])
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])

    reasons = set()
    warnings = []
    choices = []
    wording = []

    main = _resolve_section(recipe.get("ingredients") or [], excluded, catalog)
    choices.extend(main["choices"])
    reasons |= main["reasons"]
    wording.extend(main["wording"])
    main_equipment = (set(recipe.get("equipment") or [])
                      | set(main["eq_add"])) - set(main["eq_remove"])

    kept_components = []
    for comp in recipe.get("components") or []:
        sec = _resolve_section(comp.get("ingredients") or [], excluded, catalog)
        choices.extend(sec["choices"])
        comp_equipment = (set(comp.get("equipment") or [])
                          | set(sec["eq_add"])) - set(sec["eq_remove"])
        missing = sorted(e for e in comp_equipment if e not in available)
        if (sec["reasons"] or missing) and comp.get("optional"):
            warnings.append("omitted optional component %s" % comp.get("name", "unnamed"))
            continue
        reasons |= sec["reasons"]
        reasons.update("equipment %s unavailable" % e for e in missing)
        wording.extend(sec["wording"])
        kept_components.append((comp, sec, comp_equipment))

    reasons.update("equipment %s unavailable" % e
                   for e in main_equipment if e not in available)

    corpus = list(recipe.get("instructions") or [])
    for comp, _sec, _eq in kept_components:
        corpus.extend(comp.get("instructions") or [])
    for pair in wording:
        if not any(pair["old"] in line for line in corpus):
            warnings.append('wording change "%s" not found' % pair["old"])

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    adapted = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": _scaled(main["ingredients"], scale),
        "equipment": sorted(main_equipment),
        "instructions": _reworded(recipe.get("instructions") or [], wording),
        "components": [
            {
                "name": comp.get("name"),
                "optional": bool(comp.get("optional")),
                "ingredients": _scaled(sec["ingredients"], scale),
                "equipment": sorted(eq),
                "instructions": _reworded(comp.get("instructions") or [], wording),
            }
            for comp, sec, eq in kept_components
        ],
        "authored_text": recipe["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }


def _selftest():
    import copy

    base = {
        "title": "Pancakes",
        "yield": Fraction(3),
        "ingredients": [
            {"id": "flour", "name": "flour", "quantity": Fraction(3, 2),
             "unit": "cup", "preparation": "sifted"},
            {"id": "butter", "name": "butter", "quantity": Fraction(3),
             "unit": "tbsp", "preparation": "melted"},
            {"id": "eggs", "name": "eggs", "quantity": Fraction(3),
             "unit": "each", "preparation": "whisked"},
        ],
        "equipment": ["whisk", "skillet"],
        "instructions": ["Whisk the eggs.", "Melt the butter.", "Combine and cook."],
        "components": [
            {"name": "berry topping", "optional": True,
             "ingredients": [
                 {"id": "honey", "name": "honey", "quantity": Fraction(1, 4),
                  "unit": "cup", "preparation": None}],
             "equipment": [],
             "instructions": ["Drizzle honey over berries."]},
        ],
        "authored_text": "Pancakes\nServes 3.\n1 1/2 cup flour, sifted\n3 tbsp butter, melted\n3 eggs, whisked\n",
    }
    snapshot = copy.deepcopy(base)

    def rq(y, eq, ex):
        return {"target_yield": y, "available_equipment": eq, "excluded": ex}

    # 1) scaling 3 -> 5 servings
    r = adapt(base, rq(Fraction(5), ["skillet", "whisk"], []), [])
    assert r["possible"] and r["recipe"]["yield"] == Fraction(5)
    assert r["recipe"]["ingredients"][0]["quantity"] == Fraction(5, 2)
    assert r["recipe"]["components"][0]["ingredients"][0]["quantity"] == Fraction(5, 12)
    assert r["reasons"] == []

    # 2) chained substitution with deterministic explicit-priority choice
    cat = [
        {"id": "sub-marg", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "sub-butter-oil", "for": "butter", "priority": 2,
         "result": {"name": "oil"}},
        {"id": "sub-oil", "for": "margarine", "priority": 1,
         "result": {"name": "oil", "quantity_factor": Fraction(3, 4), "unit": "tbsp"}},
    ]
    r = adapt(base, rq(Fraction(3), ["skillet", "whisk"], ["butter", "margarine"]), cat)
    assert r["possible"] and r["choices"] == ["sub-marg", "sub-oil"]
    oil = [i for i in r["recipe"]["ingredients"] if i["name"] == "oil"][0]
    assert oil["quantity"] == Fraction(9, 4)

    # 3) substitution changing a referenced verb and equipment
    cat3 = [{"id": "sub-flax", "for": "eggs", "priority": 1,
             "result": {"name": "flax egg", "preparation": "soaked",
                        "wording_changes": [{"old": "Whisk", "new": "Stir"}],
                        "equipment_add": ["blender"],
                        "equipment_remove": ["whisk"]}}]
    r = adapt(base, rq(Fraction(3), ["blender", "skillet"], ["eggs"]), cat3)
    assert r["possible"]
    assert r["recipe"]["instructions"][0] == "Stir the eggs."
    assert r["recipe"]["equipment"] == ["blender", "skillet"]

    # 4) exclusion reintroduced by an additional ingredient
    cat4 = [{"id": "sub-marg2", "for": "butter", "priority": 1,
             "result": {"name": "margarine",
                        "additional_ingredients": [
                            {"id": "honey2", "name": "honey",
                             "quantity": Fraction(1, 2), "unit": "tbsp",
                             "preparation": None}]}}]
    r = adapt(base, rq(Fraction(3), ["skillet", "whisk"], ["butter", "honey"]), cat4)
    assert not r["possible"] and r["recipe"] is None
    assert "no substitution for honey" in r["reasons"]
    assert "omitted optional component berry topping" in r["warnings"]

    # 5) multiple simultaneous impossibility reasons, all reported
    r = adapt(base, rq(Fraction(3), ["blender"], ["eggs"]), [])
    assert r["reasons"] == ["equipment skillet unavailable",
                            "equipment whisk unavailable",
                            "no substitution for eggs"]
    assert r["recipe"] is None

    # 6) substitution cycle detection
    cat6 = [{"id": "a", "for": "butter", "priority": 1, "result": {"name": "margarine"}},
            {"id": "b", "for": "margarine", "priority": 1, "result": {"name": "butter"}}]
    r = adapt(base, rq(Fraction(3), ["skillet", "whisk"], ["butter", "margarine"]), cat6)
    assert not r["possible"]
    assert r["reasons"] == ["substitution cycle involving butter"]

    # 7) inputs untouched; original prints byte-for-byte after adaptations
    assert base == snapshot
    assert print_original(base) == base["authored_text"]
    assert print_original(base) == snapshot["authored_text"]

    print("all checks passed")


if __name__ == "__main__":
    _selftest()
