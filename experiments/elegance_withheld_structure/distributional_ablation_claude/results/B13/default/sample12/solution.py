"""recipebook.py -- adapt recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog, using only the stdlib.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Catalog ``result`` keys: name, quantity_factor, unit, preparation,
yield_factor, wording_changes [{old,new}], add_equipment, remove_equipment,
additional_ingredients (full ingredient dicts).
"""

from fractions import Fraction
import copy


def print_original(recipe):
    """Return the recipe exactly as authored, byte for byte."""
    return recipe["authored_text"]


def _candidates(name, catalog):
    """Catalog choices for ``name``, best first: lower priority wins,
    catalog order breaks ties."""
    ranked = [(c.get("priority", 0), i, c)
              for i, c in enumerate(catalog) if c.get("for") == name]
    ranked.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in ranked]


def adapt(recipe, request, catalog):
    work = copy.deepcopy(recipe)
    target = Fraction(request["target_yield"])
    available = set(request.get("available_equipment") or [])
    excluded = set(request.get("excluded") or [])

    choices = []            # ordered catalog IDs as applied
    warnings = []
    reasons = set()
    wording_changes = []    # ordered {old,new} pairs from applied results
    equip_add = []
    equip_remove = []
    extra_queue = []        # (ingredient dict, inherited chain set)
    yield_factor = Fraction(1)

    def resolve(ing, chain):
        nonlocal yield_factor
        while ing.get("name") in excluded:
            name = ing["name"]
            if name in chain:
                reasons.add("substitution cycle involving " + name)
                return
            chain.add(name)
            cands = _candidates(name, catalog)
            if not cands:
                reasons.add("no substitution for " + name)
                return
            choice = cands[0]
            choices.append(choice["id"])
            res = choice["result"]
            old_unit = ing.get("unit")
            ing["name"] = res["name"]
            if "quantity_factor" in res and ing.get("quantity") is not None:
                ing["quantity"] = (Fraction(ing["quantity"])
                                   * Fraction(res["quantity_factor"]))
            if "unit" in res:
                if res["unit"] != old_unit:
                    warnings.append("unit changed for %s: %s -> %s"
                                    % (res["name"], old_unit, res["unit"]))
                ing["unit"] = res["unit"]
            if "preparation" in res:
                ing["preparation"] = res["preparation"]
            if "yield_factor" in res:
                yield_factor *= Fraction(res["yield_factor"])
                warnings.append("yield adjusted by substitution "
                                + str(choice["id"]))
            for wc in res.get("wording_changes") or []:
                wording_changes.append({"old": wc["old"], "new": wc["new"]})
            for e in res.get("add_equipment") or []:
                equip_add.append(e)
                warnings.append("equipment added: " + e)
            for e in res.get("remove_equipment") or []:
                equip_remove.append(e)
                warnings.append("equipment removed: " + e)
            for extra in res.get("additional_ingredients") or []:
                extra_queue.append((copy.deepcopy(extra), set(chain)))

    # Main ingredients plus any structured component ingredients are all
    # subject to the same exclusions, substitutions, and scaling.
    ingredient_lists = [work.setdefault("ingredients", [])]
    for comp in work.get("components") or []:
        if isinstance(comp, dict) and comp.get("ingredients"):
            ingredient_lists.append(comp["ingredients"])

    for ings in ingredient_lists:
        for ing in ings:
            resolve(ing, set())

    while extra_queue:
        extra, chain = extra_queue.pop(0)
        extra.setdefault("id", extra["name"])
        extra.setdefault("quantity", Fraction(1))
        extra.setdefault("unit", "")
        extra.setdefault("preparation", "")
        resolve(extra, chain)
        work["ingredients"].append(extra)

    # Removals apply to the authored list; additions win over removals of
    # the same item so a later substitution can restore a tool.
    equipment = sorted((set(work.get("equipment") or []) - set(equip_remove))
                       | set(equip_add))
    for e in equipment:
        if e not in available:
            reasons.add("equipment " + e + " unavailable")

    if reasons:
        return {"possible": False, "recipe": None, "choices": choices,
                "warnings": sorted(warnings), "reasons": sorted(reasons)}

    # One uniform scale makes every quantity reflect both the yield change
    # and any yield_factor a substitution introduced.
    scale = target / (Fraction(work["yield"]) * yield_factor)
    for ings in ingredient_lists:
        for ing in ings:
            if ing.get("quantity") is not None:
                ing["quantity"] = Fraction(ing["quantity"]) * scale

    def rewrite(lines, wc, found):
        out = []
        for s in lines:
            if wc["old"] in s:
                found[0] = True
                s = s.replace(wc["old"], wc["new"])
            out.append(s)
        return out

    instructions = list(work.get("instructions") or [])
    for wc in wording_changes:
        found = [False]
        instructions = rewrite(instructions, wc, found)
        for comp in work.get("components") or []:
            if isinstance(comp, dict) and comp.get("instructions"):
                comp["instructions"] = rewrite(comp["instructions"], wc, found)
        if not found[0]:
            warnings.append("wording change not applied: " + wc["old"])

    adapted = {
        "title": work.get("title"),
        "yield": target,
        "ingredients": work["ingredients"],
        "equipment": equipment,
        "instructions": instructions,
        "components": work.get("components") or [],
        "authored_text": work.get("authored_text"),
    }
    return {"possible": True, "recipe": adapted, "choices": choices,
            "warnings": sorted(warnings), "reasons": []}


if __name__ == "__main__":
    def base_recipe():
        return {
            "title": "Pancakes",
            "yield": Fraction(3),
            "ingredients": [
                {"id": "flour", "name": "wheat flour",
                 "quantity": Fraction(3, 2), "unit": "cup",
                 "preparation": "sifted"},
                {"id": "milk", "name": "milk", "quantity": Fraction(1),
                 "unit": "cup", "preparation": ""},
                {"id": "butter", "name": "butter", "quantity": Fraction(2),
                 "unit": "tbsp", "preparation": "melted"},
            ],
            "equipment": ["whisk", "skillet"],
            "instructions": [
                "Whisk the milk into the sifted wheat flour.",
                "Melt the butter in the skillet.",
            ],
            "components": [],
            "authored_text": ("Pancakes\nServes 3.\n"
                              "Whisk the milk into the sifted wheat flour.\n"
                              "Melt the butter in the skillet.\n"),
        }

    # 1. Scaling 3 -> 5 servings.
    r = base_recipe()
    snap = copy.deepcopy(r)
    out = adapt(r, {"target_yield": Fraction(5),
                    "available_equipment": ["whisk", "skillet"],
                    "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == Fraction(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == Fraction(5, 2)
    assert out["recipe"]["ingredients"][1]["quantity"] == Fraction(5, 3)

    # 2. Chained substitution that also changes a referenced verb and the
    #    equipment (butter -> margarine -> olive oil).
    chain_catalog = [
        {"id": "butter->marg", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "marg->oil", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "quantity_factor": Fraction(3, 4),
                    "unit": "tbsp", "preparation": "",
                    "wording_changes": [
                        {"old": "Melt the butter in the skillet",
                         "new": "Heat the olive oil in the saucepan"}],
                    "add_equipment": ["saucepan"],
                    "remove_equipment": ["skillet"]}},
    ]
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["whisk", "saucepan"],
                    "excluded": ["butter", "margarine"]}, chain_catalog)
    assert out["possible"]
    assert out["choices"] == ["butter->marg", "marg->oil"]
    oil = out["recipe"]["ingredients"][2]
    assert oil["name"] == "olive oil" and oil["quantity"] == Fraction(3, 2)
    assert out["recipe"]["equipment"] == ["saucepan", "whisk"]
    assert out["recipe"]["instructions"][1] == \
        "Heat the olive oil in the saucepan."
    assert out["warnings"] == sorted(out["warnings"])

    # 3. An exclusion reintroduced later: the milk substitute adds honey,
    #    which is itself excluded and must be substituted in turn.
    reintro_catalog = [
        {"id": "milk->cond", "for": "milk", "priority": 1,
         "result": {"name": "condensed milk",
                    "additional_ingredients": [
                        {"id": "honey", "name": "honey",
                         "quantity": Fraction(1, 4), "unit": "cup",
                         "preparation": ""}]}},
        {"id": "honey->maple", "for": "honey", "priority": 1,
         "result": {"name": "maple syrup"}},
    ]
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["whisk", "skillet"],
                    "excluded": ["milk", "honey"]}, reintro_catalog)
    assert out["possible"]
    assert out["choices"] == ["milk->cond", "honey->maple"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "maple syrup" in names
    assert "milk" not in names and "honey" not in names

    # 3b. Substitution cycle detection (milk -> soy milk -> milk).
    cycle_catalog = [
        {"id": "m->s", "for": "milk", "priority": 1,
         "result": {"name": "soy milk"}},
        {"id": "s->m", "for": "soy milk", "priority": 1,
         "result": {"name": "milk"}},
    ]
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["whisk", "skillet"],
                    "excluded": ["milk", "soy milk"]}, cycle_catalog)
    assert not out["possible"] and out["recipe"] is None
    assert "substitution cycle involving milk" in out["reasons"]

    # 4. Multiple simultaneous impossibility reasons, all reported, sorted.
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": [],
                    "excluded": ["milk"]}, [])
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["equipment skillet unavailable",
                              "equipment whisk unavailable",
                              "no substitution for milk"]

    # 5. Original prints byte-for-byte after adaptations; inputs unmutated.
    assert print_original(r) == snap["authored_text"]
    assert r == snap

    # 6. Deterministic choice by explicit catalog priority (lower wins),
    #    catalog order as tie-breaker.
    prio_catalog = [
        {"id": "milk->oat", "for": "milk", "priority": 2,
         "result": {"name": "oat milk"}},
        {"id": "milk->soy", "for": "milk", "priority": 1,
         "result": {"name": "soy milk"}},
        {"id": "milk->rice", "for": "milk", "priority": 1,
         "result": {"name": "rice milk"}},
    ]
    out = adapt(r, {"target_yield": Fraction(3),
                    "available_equipment": ["whisk", "skillet"],
                    "excluded": ["milk"]}, prio_catalog)
    assert out["possible"] and out["choices"] == ["milk->soy"]
    assert out["recipe"]["ingredients"][1]["name"] == "soy milk"
    assert r == snap

    print("all acceptance checks passed")
