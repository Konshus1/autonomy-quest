"""recipebook.py -- adapt structured recipes to yield, equipment, and diet.

Quantities and yields are fractions.Fraction throughout.  Only the Python
standard library is used.
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as authored, byte for byte."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    """Adapt ``recipe`` to ``request`` using ``catalog`` substitutions.

    Returns {"possible", "recipe", "choices", "warnings", "reasons"}.
    Inputs are never mutated.
    """
    adapted = copy.deepcopy(recipe)
    excluded = set(request.get("excluded") or [])
    available = set(request.get("available_equipment") or [])

    choices = []
    warnings = set()
    reasons = set()

    # Lower numeric priority wins; catalog order breaks ties.
    ranked = sorted(range(len(catalog)),
                    key=lambda i: (catalog[i].get("priority", 0), i))

    def pick(name):
        for i in ranked:
            if catalog[i].get("for") == name:
                return catalog[i]
        return None

    target = Fraction(request["target_yield"])
    scale = target / Fraction(adapted["yield"])
    adapted["yield"] = target

    def rescale(ings, factor):
        for ing in ings:
            ing["quantity"] = Fraction(ing["quantity"]) * factor

    rescale(adapted["ingredients"], scale)
    for comp in adapted.get("components") or []:
        rescale(comp.setdefault("ingredients", []), scale)

    equipment = set(adapted.get("equipment") or [])
    instructions = list(adapted.get("instructions") or [])
    yield_drift = Fraction(1)

    def apply_wording(pairs):
        nonlocal instructions
        for pair in pairs:
            old, new = pair["old"], pair["new"]
            if not any(old in line for line in instructions):
                warnings.add("wording '%s' not found" % old)
            instructions = [line.replace(old, new) for line in instructions]

    def resolve(ing, chain, sink, extra_rows):
        nonlocal yield_drift
        name = ing["name"]
        if name in chain:
            sink.add("substitution cycle involving %s" % name)
            return
        choice = pick(name)
        if choice is None:
            sink.add("no substitution for %s" % name)
            return
        chain = chain | {name}
        choices.append(choice["id"])
        res = choice["result"]
        ing["name"] = res["name"]
        if "quantity_factor" in res:
            ing["quantity"] = ing["quantity"] * Fraction(res["quantity_factor"])
        if "unit" in res:
            ing["unit"] = res["unit"]
        if "preparation" in res:
            ing["preparation"] = res["preparation"]
        apply_wording(res.get("wording_changes") or [])
        for item in res.get("equipment_add") or []:
            equipment.add(item)
        for item in res.get("equipment_remove") or []:
            equipment.discard(item)
        if "yield_factor" in res:
            yield_drift *= Fraction(res["yield_factor"])
            warnings.add("yield rescaled after substitution %s" % choice["id"])
        fresh = []
        for extra in res.get("additional_ingredients") or []:
            row = {
                "id": extra.get("id", extra["name"]),
                "name": extra["name"],
                "quantity": Fraction(extra["quantity"]) * scale,
                "unit": extra.get("unit", ""),
                "preparation": extra.get("preparation", ""),
            }
            fresh.append(row)
            extra_rows.append(row)
        if ing["name"] in excluded:
            warnings.add("substitution %s reintroduced excluded ingredient %s"
                         % (choice["id"], ing["name"]))
            resolve(ing, chain, sink, extra_rows)
        for row in fresh:
            if row["name"] in excluded:
                warnings.add("substitution %s reintroduced excluded ingredient %s"
                             % (choice["id"], row["name"]))
                resolve(row, chain, sink, extra_rows)

    def sweep(ings, sink):
        extra_rows = []
        for ing in ings:
            if ing["name"] in excluded:
                resolve(ing, frozenset(), sink, extra_rows)
        ings.extend(extra_rows)

    sweep(adapted["ingredients"], reasons)

    kept = []
    for comp in adapted.get("components") or []:
        local = set()
        sweep(comp["ingredients"], local)
        if local and comp.get("optional"):
            warnings.add("omitted optional component %s"
                         % comp.get("name", "unnamed"))
        else:
            reasons |= local
            kept.append(comp)
    adapted["components"] = kept

    # Substitutions that alter yield are compensated so the final recipe
    # still lands exactly on the requested target yield.
    if yield_drift != 1:
        rescale(adapted["ingredients"], 1 / yield_drift)
        for comp in adapted["components"]:
            rescale(comp["ingredients"], 1 / yield_drift)

    for item in equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)

    adapted["equipment"] = sorted(equipment)
    adapted["instructions"] = instructions

    return {
        "possible": not reasons,
        "recipe": adapted if not reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }


def _demo():
    return {
        "title": "Pan Sauce",
        "yield": Fraction(3),
        "ingredients": [
            {"id": "flour", "name": "flour", "quantity": Fraction(2),
             "unit": "cup", "preparation": "sifted"},
            {"id": "butter", "name": "butter", "quantity": Fraction(3),
             "unit": "tbsp", "preparation": "melted"},
            {"id": "cream", "name": "cream", "quantity": Fraction(1),
             "unit": "cup", "preparation": "chilled"},
        ],
        "equipment": ["skillet", "whisk"],
        "instructions": ["Melt the butter in the skillet.",
                         "Whip the cream with the whisk."],
        "components": [],
        "authored_text": "Pan Sauce\nServes 3.\nMelt the butter in the "
                         "skillet.\nWhip the cream with the whisk.\n",
    }


if __name__ == "__main__":
    all_eq = ["skillet", "whisk", "saucepan"]

    # Scaling 3 -> 5 servings keeps exact rationals.
    out = adapt(_demo(), {"target_yield": Fraction(5),
                          "available_equipment": all_eq,
                          "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == Fraction(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == Fraction(10, 3)

    # Chained substitution with a reintroduced exclusion, plus a
    # substitution that changes a referenced verb and the equipment.
    cat = [
        {"id": "b2m", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "m2o", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "unit": "tbsp",
                    "preparation": "warmed",
                    "wording_changes": [{"old": "Melt the butter",
                                         "new": "Warm the olive oil"}]}},
        {"id": "c2c", "for": "cream", "priority": 1,
         "result": {"name": "coconut milk",
                    "wording_changes": [
                        {"old": "Whip the cream with the whisk",
                         "new": "Warm the coconut milk in the saucepan"}],
                    "equipment_remove": ["whisk"],
                    "equipment_add": ["saucepan"]}},
    ]
    out = adapt(_demo(), {"target_yield": Fraction(3),
                          "available_equipment": ["skillet", "saucepan"],
                          "excluded": ["butter", "margarine", "cream"]}, cat)
    assert out["possible"]
    assert out["choices"] == ["b2m", "m2o", "c2c"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "olive oil" in names and "coconut milk" in names
    assert out["recipe"]["instructions"] == [
        "Warm the olive oil in the skillet.",
        "Warm the coconut milk in the saucepan."]
    assert out["recipe"]["equipment"] == ["saucepan", "skillet"]
    assert any("reintroduced excluded ingredient margarine" in w
               for w in out["warnings"])

    # Multiple simultaneous impossibility reasons, sorted.
    out = adapt(_demo(), {"target_yield": Fraction(3),
                          "available_equipment": ["skillet"],
                          "excluded": ["flour", "cream"]}, [])
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["equipment whisk unavailable",
                              "no substitution for cream",
                              "no substitution for flour"]

    # Substitution cycle detection.
    cyc = [
        {"id": "b2m", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "m2b", "for": "margarine", "priority": 1,
         "result": {"name": "butter"}},
    ]
    out = adapt(_demo(), {"target_yield": Fraction(3),
                          "available_equipment": all_eq,
                          "excluded": ["butter", "margarine"]}, cyc)
    assert out["reasons"] == ["substitution cycle involving butter"]

    # Deterministic choice: lowest priority wins, catalog order breaks ties.
    pri = [
        {"id": "worse", "for": "butter", "priority": 2,
         "result": {"name": "lard"}},
        {"id": "best", "for": "butter", "priority": 1,
         "result": {"name": "olive oil"}},
        {"id": "tied", "for": "butter", "priority": 1,
         "result": {"name": "ghee"}},
    ]
    out = adapt(_demo(), {"target_yield": Fraction(3),
                          "available_equipment": all_eq,
                          "excluded": ["butter"]}, pri)
    assert out["choices"] == ["best"]
    assert out["recipe"]["ingredients"][1]["name"] == "olive oil"

    # Original is untouched and prints byte-for-byte after adaptation.
    original = _demo()
    snapshot = copy.deepcopy(original)
    adapt(original, {"target_yield": Fraction(5),
                     "available_equipment": all_eq,
                     "excluded": ["butter"]}, cat)
    assert original == snapshot
    assert print_original(original) == snapshot["authored_text"]
    print("all acceptance checks passed")
