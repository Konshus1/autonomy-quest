"""recipebook: adapt authored recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog.

Quantities and yields are fractions.Fraction; only the standard library is
used.  adapt() never mutates its arguments and print_original() returns the
authored text byte-for-byte.
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as authored."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    """Adapt *recipe* to *request* using *catalog*.

    Returns {"possible", "recipe", "choices", "warnings", "reasons"}.
    """
    work = copy.deepcopy(recipe)
    target = Fraction(request["target_yield"])
    available = set(request["available_equipment"])
    excluded = set(request["excluded"])

    choices = []
    warnings = set()
    reasons = set()

    scale = target / Fraction(work["yield"])
    work["yield"] = target
    for section in _sections(work):
        for ing in section.get("ingredients") or []:
            ing["quantity"] = Fraction(ing["quantity"]) * scale

    ranked = {}
    for index, choice in enumerate(catalog):
        ranked.setdefault(choice["for"], []).append(
            (choice.get("priority", 0), index, choice))
    for options in ranked.values():
        options.sort(key=lambda entry: (entry[0], entry[1]))

    for section in _sections(work):
        ingredients = section.get("ingredients")
        if not ingredients:
            continue
        i = 0
        while i < len(ingredients):  # list grows when substitutions add ingredients
            _resolve(ingredients[i], section, work, excluded, ranked, scale,
                     choices, warnings, reasons)
            i += 1

    for section in _sections(work):
        if section.get("equipment") is not None:
            section["equipment"] = sorted(set(section["equipment"]))
            for name in section["equipment"]:
                if name not in available:
                    reasons.add("equipment " + name + " unavailable")

    if reasons:
        return {"possible": False, "recipe": None, "choices": choices,
                "warnings": sorted(warnings), "reasons": sorted(reasons)}
    return {"possible": True, "recipe": work, "choices": choices,
            "warnings": sorted(warnings), "reasons": []}


def _sections(work):
    yield work
    for component in work.get("components") or []:
        if isinstance(component, dict):
            yield component


def _resolve(ing, section, work, excluded, ranked, scale,
             choices, warnings, reasons):
    seen = {ing["name"]}
    while ing["name"] in excluded:
        options = ranked.get(ing["name"])
        if not options:
            reasons.add("no substitution for " + ing["name"])
            return
        choice = options[0][2]
        choices.append(choice["id"])
        _apply_result(ing, choice["result"], section, work, scale, warnings)
        if ing["name"] in seen:
            reasons.add("substitution cycle involving " + ing["name"])
            return
        seen.add(ing["name"])


def _apply_result(ing, result, section, work, scale, warnings):
    ing["name"] = result["name"]
    if "quantity_factor" in result:
        ing["quantity"] = ing["quantity"] * Fraction(result["quantity_factor"])
    if "unit" in result:
        if result["unit"] != ing.get("unit"):
            warnings.add("unit changed for " + result["name"])
        ing["unit"] = result["unit"]
    if "preparation" in result:
        ing["preparation"] = result["preparation"]

    for pair in result.get("wording_changes") or []:
        for sec in _sections(work):
            if sec.get("instructions"):
                sec["instructions"] = [line.replace(pair["old"], pair["new"])
                                       for line in sec["instructions"]]

    for name in result.get("equipment_add") or []:
        equipment = work.setdefault("equipment", [])
        if name not in equipment:
            equipment.append(name)
            warnings.add("equipment added: " + name)
    for name in result.get("equipment_remove") or []:
        if name in (work.get("equipment") or []):
            work["equipment"].remove(name)
            warnings.add("equipment removed: " + name)

    if "yield_factor" in result:
        # The replacement changes how much the recipe makes; compensate every
        # quantity so the adapted recipe still lands on the requested yield.
        compensation = 1 / Fraction(result["yield_factor"])
        for sec in _sections(work):
            for other in sec.get("ingredients") or []:
                other["quantity"] = other["quantity"] * compensation
        warnings.add("yield rescaled for " + result["name"])

    for extra in result.get("additional_ingredients") or []:
        added = copy.deepcopy(extra)
        added["quantity"] = Fraction(added.get("quantity", 1)) * scale
        added.setdefault("unit", "")
        added.setdefault("preparation", "")
        added.setdefault("id", added["name"])
        section["ingredients"].append(added)
        warnings.add("ingredient added: " + added["name"])


def _selftest():
    F = Fraction
    base = {
        "title": "Herb Butter Pasta",
        "yield": F(3),
        "ingredients": [
            {"id": "pasta", "name": "pasta", "quantity": F(300), "unit": "g",
             "preparation": "boiled until tender"},
            {"id": "butter", "name": "butter", "quantity": F(3, 2),
             "unit": "tbsp", "preparation": "melted"},
        ],
        "equipment": ["saucepan", "whisk"],
        "instructions": ["Melt the butter in the saucepan.",
                         "Whisk the butter into the pasta."],
        "components": [
            {"name": "garnish",
             "ingredients": [{"id": "parsley", "name": "parsley",
                              "quantity": F(1), "unit": "bunch",
                              "preparation": "chopped"}],
             "instructions": ["Sprinkle the parsley over the top."]}],
        "authored_text": ("Herb Butter Pasta\nServes 3\n300 g pasta, boiled"
                          " until tender\n1 1/2 tbsp butter, melted\n"),
    }
    snapshot = copy.deepcopy(base)
    everything = ["saucepan", "whisk", "small pot"]

    # scaling 3 -> 5 servings
    out = adapt(base, {"target_yield": F(5), "available_equipment": everything,
                       "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    got = {i["name"]: i["quantity"] for i in out["recipe"]["ingredients"]}
    assert got == {"pasta": F(500), "butter": F(5, 2)}
    assert out["recipe"]["components"][0]["ingredients"][0]["quantity"] == F(5, 3)
    assert out["recipe"]["yield"] == F(5)

    # substitution changing a referenced verb and equipment
    verb_catalog = [{"id": "b2o", "for": "butter", "priority": 1, "result": {
        "name": "olive oil", "quantity_factor": F(3, 4),
        "preparation": "warmed",
        "wording_changes": [{"old": "Melt the butter",
                             "new": "Warm the olive oil"},
                            {"old": "Whisk the butter",
                             "new": "Whisk the olive oil"}],
        "equipment_add": ["small pot"]}}]
    out = adapt(base, {"target_yield": F(3), "available_equipment": everything,
                       "excluded": ["butter"]}, verb_catalog)
    assert out["possible"] and out["choices"] == ["b2o"]
    assert out["recipe"]["instructions"] == [
        "Warm the olive oil in the saucepan.",
        "Whisk the olive oil into the pasta."]
    assert out["recipe"]["equipment"] == ["saucepan", "small pot", "whisk"]
    oil = next(i for i in out["recipe"]["ingredients"]
               if i["name"] == "olive oil")
    assert oil["quantity"] == F(9, 8) and oil["preparation"] == "warmed"

    # chained substitution: a replacement introduces another excluded
    # ingredient, which is then substituted in turn
    chain_catalog = [
        {"id": "b2v", "for": "butter", "priority": 1, "result": {
            "name": "vegan spread",
            "additional_ingredients": [{"id": "cream", "name": "cream",
                                        "quantity": F(1, 4), "unit": "cup"}]}},
        {"id": "c2o", "for": "cream", "priority": 1,
         "result": {"name": "oat cream"}},
    ]
    out = adapt(base, {"target_yield": F(3), "available_equipment": everything,
                       "excluded": ["butter", "cream"]}, chain_catalog)
    assert out["possible"] and out["choices"] == ["b2v", "c2o"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "vegan spread" in names and "oat cream" in names
    assert "cream" not in names

    # exclusion reintroduced later, looping back: substitution cycle
    cycle_catalog = [
        {"id": "b2m", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "m2b", "for": "margarine", "priority": 1,
         "result": {"name": "butter"}},
    ]
    out = adapt(base, {"target_yield": F(3), "available_equipment": everything,
                       "excluded": ["butter", "margarine"]}, cycle_catalog)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["substitution cycle involving butter"]

    # multiple simultaneous impossibility reasons, all reported and sorted
    out = adapt(base, {"target_yield": F(3),
                       "available_equipment": ["saucepan"],
                       "excluded": ["butter"]}, [])
    assert not out["possible"]
    assert out["reasons"] == ["equipment whisk unavailable",
                              "no substitution for butter"]

    # deterministic choice: lower priority wins, catalog order breaks ties
    tie_catalog = [
        {"id": "late-low", "for": "butter", "priority": 2,
         "result": {"name": "ghee spread"}},
        {"id": "first-tie", "for": "butter", "priority": 1,
         "result": {"name": "olive oil"}},
        {"id": "second-tie", "for": "butter", "priority": 1,
         "result": {"name": "coconut oil"}},
    ]
    out = adapt(base, {"target_yield": F(3), "available_equipment": everything,
                       "excluded": ["butter"]}, tie_catalog)
    assert out["possible"] and out["choices"] == ["first-tie"]

    # originals stay printable exactly as authored; inputs were never mutated
    assert print_original(base) == base["authored_text"]
    assert base == snapshot


if __name__ == "__main__":
    _selftest()
    print("all acceptance checks passed")
