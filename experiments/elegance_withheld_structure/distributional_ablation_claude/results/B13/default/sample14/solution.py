"""recipebook.py

Recipe adaptation with rational quantities (fractions.Fraction), pure stdlib.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Catalog ``result`` keys understood beyond the replacement-ingredient fields
(name, quantity_factor, unit, preparation):
    wording_changes          list of {"old": str, "new": str} applied to instructions
    add_equipment            list of equipment names the substitute needs
    remove_equipment         list of equipment names no longer needed
    additional_ingredients   list of {name, quantity, unit?, preparation?, id?};
                             quantities are authored at the recipe's original yield
    yield_factor             Fraction; the whole recipe is rescaled so the adapted
                             recipe still lands exactly on the target yield
"""

from fractions import Fraction


def print_original(recipe):
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    target = Fraction(request["target_yield"])
    factor = target / Fraction(recipe["yield"])
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])

    choices = []
    warnings = set()
    reasons = set()
    equipment = set(recipe["equipment"])
    instructions = list(recipe["instructions"])
    post = [Fraction(1)]  # compensation so yield_factor substitutions still hit target

    def ranked(name):
        found = [(c["priority"], i, c) for i, c in enumerate(catalog) if c["for"] == name]
        found.sort(key=lambda t: (t[0], t[1]))
        return [c for _, _, c in found]

    def resolve(ing, seen, sink):
        cur = dict(ing)
        cur["quantity"] = Fraction(cur["quantity"]) * factor
        extras_out = []
        ok = True
        while cur["name"] in excluded:
            name = cur["name"]
            if name in seen:
                sink.add("substitution cycle involving " + name)
                return None
            seen.add(name)
            options = ranked(name)
            if not options:
                sink.add("no substitution for " + name)
                return None
            choice = options[0]
            choices.append(choice["id"])
            res = choice["result"]
            cur = dict(cur, name=res["name"])
            if "quantity_factor" in res:
                cur["quantity"] *= Fraction(res["quantity_factor"])
            if "unit" in res:
                cur["unit"] = res["unit"]
            if "preparation" in res:
                cur["preparation"] = res["preparation"]
            for wc in res.get("wording_changes", ()):
                instructions[:] = [line.replace(wc["old"], wc["new"]) for line in instructions]
            equipment.update(res.get("add_equipment", ()))
            equipment.difference_update(res.get("remove_equipment", ()))
            if "yield_factor" in res:
                yf = Fraction(res["yield_factor"])
                if yf != 1:
                    post[0] /= yf
                    warnings.add(
                        "substitution %s changes yield; quantities rescaled to target"
                        % choice["id"]
                    )
            for extra in res.get("additional_ingredients", ()):
                e = {
                    "id": extra.get("id", extra["name"].lower().replace(" ", "-")),
                    "name": extra["name"],
                    "quantity": Fraction(extra["quantity"]),
                    "unit": extra.get("unit", ""),
                    "preparation": extra.get("preparation", ""),
                }
                if e["name"] in excluded:
                    warnings.add(
                        "substitution %s introduces excluded ingredient %s"
                        % (choice["id"], e["name"])
                    )
                got = resolve(e, set(seen), sink)
                if got is None:
                    ok = False
                else:
                    extras_out.extend(got)
            if cur["name"] in excluded:
                warnings.add(
                    "substitution %s introduces excluded ingredient %s"
                    % (choice["id"], cur["name"])
                )
        if not ok:
            return None
        return [cur] + extras_out

    ingredients = []
    for ing in recipe["ingredients"]:
        got = resolve(ing, set(), reasons)
        if got is not None:
            ingredients.extend(got)

    components = []
    for comp in recipe.get("components", ()):
        optional = bool(comp.get("optional"))
        sink = set() if optional else reasons
        comp_ings = []
        comp_ok = True
        for ing in comp.get("ingredients", ()):
            got = resolve(ing, set(), sink)
            if got is None:
                comp_ok = False
            else:
                comp_ings.extend(got)
        if comp_ok:
            new_comp = {k: v for k, v in comp.items() if k != "ingredients"}
            new_comp["ingredients"] = comp_ings
            components.append(new_comp)
        elif optional:
            warnings.add("optional component %s omitted" % comp.get("name", "?"))

    for eq in sorted(equipment):
        if eq not in available:
            reasons.add("equipment %s unavailable" % eq)

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    if post[0] != 1:
        for ing in ingredients:
            ing["quantity"] *= post[0]
        for comp in components:
            for ing in comp["ingredients"]:
                ing["quantity"] *= post[0]

    adapted = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": ingredients,
        "equipment": sorted(equipment),
        "instructions": instructions,
        "components": components,
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

def _demo_recipe():
    return {
        "title": "Herb Pancakes",
        "yield": Fraction(3),
        "ingredients": [
            {"id": "flour", "name": "wheat flour", "quantity": Fraction(3, 2),
             "unit": "cup", "preparation": "sifted"},
            {"id": "butter", "name": "butter", "quantity": Fraction(3),
             "unit": "tbsp", "preparation": "melted"},
            {"id": "milk", "name": "milk", "quantity": Fraction(3, 4),
             "unit": "cup", "preparation": ""},
        ],
        "equipment": ["whisk", "skillet"],
        "instructions": [
            "Whisk the sifted wheat flour with the milk.",
            "Melt the butter in the skillet.",
        ],
        "components": [
            {"name": "herb topping", "optional": True, "ingredients": [
                {"id": "chives", "name": "chives", "quantity": Fraction(1, 4),
                 "unit": "cup", "preparation": "chopped"},
            ]},
        ],
        "authored_text": (
            "Herb Pancakes\nServes 3.\n"
            "1 1/2 cup wheat flour, sifted\n3 tbsp butter, melted\n3/4 cup milk\n"
            "Whisk the sifted wheat flour with the milk.\n"
            "Melt the butter in the skillet.\n"
            "Optional herb topping: 1/4 cup chives, chopped.\n"
        ),
    }


def _request(target=Fraction(5), equipment=("whisk", "skillet"), excluded=()):
    return {"target_yield": target,
            "available_equipment": list(equipment),
            "excluded": list(excluded)}


def _test_scaling_3_to_5():
    out = adapt(_demo_recipe(), _request(), [])
    assert out["possible"] and out["reasons"] == [] and out["choices"] == []
    r = out["recipe"]
    assert r["yield"] == Fraction(5)
    q = {i["id"]: i["quantity"] for i in r["ingredients"]}
    assert q == {"flour": Fraction(5, 2), "butter": Fraction(5), "milk": Fraction(5, 4)}
    assert r["components"][0]["ingredients"][0]["quantity"] == Fraction(5, 12)
    assert r["equipment"] == ["skillet", "whisk"]


def _test_chained_substitution():
    catalog = [
        {"id": "sub-butter-marg", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "sub-marg-oil", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "quantity_factor": Fraction(3, 4),
                    "unit": "tbsp"}},
    ]
    out = adapt(_demo_recipe(), _request(excluded=["butter", "margarine"]), catalog)
    assert out["possible"]
    assert out["choices"] == ["sub-butter-marg", "sub-marg-oil"]
    fat = [i for i in out["recipe"]["ingredients"] if i["id"] == "butter"][0]
    assert fat["name"] == "olive oil"
    assert fat["quantity"] == Fraction(15, 4)  # 3 * 5/3 * 3/4
    assert fat["unit"] == "tbsp"
    assert ("substitution sub-butter-marg introduces excluded ingredient margarine"
            in out["warnings"])


def _verb_equipment_catalog():
    return [
        {"id": "sub-butter-oil", "for": "butter", "priority": 1,
         "result": {
             "name": "olive oil",
             "preparation": "",
             "wording_changes": [
                 {"old": "Melt the butter in the skillet.",
                  "new": "Warm the olive oil in the saucepan."},
             ],
             "add_equipment": ["saucepan"],
             "remove_equipment": ["skillet"],
         }},
    ]


def _test_verb_and_equipment_change():
    req = _request(equipment=("whisk", "saucepan"), excluded=["butter"])
    out = adapt(_demo_recipe(), req, _verb_equipment_catalog())
    assert out["possible"]
    assert "Warm the olive oil in the saucepan." in out["recipe"]["instructions"]
    assert all("Melt the butter" not in s for s in out["recipe"]["instructions"])
    assert out["recipe"]["equipment"] == ["saucepan", "whisk"]


def _test_reintroduced_exclusion():
    catalog = [
        {"id": "sub-b", "for": "butter", "priority": 1,
         "result": {"name": "cashew cream",
                    "additional_ingredients": [
                        {"name": "heavy cream", "quantity": Fraction(1, 2),
                         "unit": "cup"},
                    ]}},
    ]
    out = adapt(_demo_recipe(), _request(excluded=["butter", "heavy cream"]), catalog)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["no substitution for heavy cream"]
    assert ("substitution sub-b introduces excluded ingredient heavy cream"
            in out["warnings"])


def _test_multiple_reasons():
    catalog = [
        {"id": "c1", "for": "butter", "priority": 1, "result": {"name": "margarine"}},
        {"id": "c2", "for": "margarine", "priority": 1, "result": {"name": "butter"}},
    ]
    req = _request(equipment=("skillet",), excluded=["milk", "butter", "margarine"])
    out = adapt(_demo_recipe(), req, catalog)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == [
        "equipment whisk unavailable",
        "no substitution for milk",
        "substitution cycle involving butter",
    ]
    assert out["choices"] == ["c1", "c2"]


def _test_original_print_and_no_mutation():
    import copy
    recipe = _demo_recipe()
    snapshot = copy.deepcopy(recipe)
    req = _request(equipment=("whisk", "saucepan"), excluded=["butter"])
    snapshot_req = copy.deepcopy(req)
    catalog = _verb_equipment_catalog()
    snapshot_cat = copy.deepcopy(catalog)
    adapt(recipe, req, catalog)
    assert recipe == snapshot and req == snapshot_req and catalog == snapshot_cat
    assert print_original(recipe) == snapshot["authored_text"]


def _test_deterministic_priority():
    catalog = [
        {"id": "b-high", "for": "butter", "priority": 5, "result": {"name": "ghee"}},
        {"id": "b-low", "for": "butter", "priority": 1, "result": {"name": "olive oil"}},
    ]
    out = adapt(_demo_recipe(), _request(excluded=["butter"]), catalog)
    assert out["choices"] == ["b-low"]
    assert [i for i in out["recipe"]["ingredients"] if i["id"] == "butter"][0]["name"] \
        == "olive oil"
    tie = [
        {"id": "first", "for": "butter", "priority": 1, "result": {"name": "ghee"}},
        {"id": "second", "for": "butter", "priority": 1, "result": {"name": "olive oil"}},
    ]
    out = adapt(_demo_recipe(), _request(excluded=["butter"]), tie)
    assert out["choices"] == ["first"]
    assert [i for i in out["recipe"]["ingredients"] if i["id"] == "butter"][0]["name"] \
        == "ghee"


if __name__ == "__main__":
    _test_scaling_3_to_5()
    _test_chained_substitution()
    _test_verb_and_equipment_change()
    _test_reintroduced_exclusion()
    _test_multiple_reasons()
    _test_original_print_and_no_mutation()
    _test_deterministic_priority()
    print("all acceptance tests passed")
