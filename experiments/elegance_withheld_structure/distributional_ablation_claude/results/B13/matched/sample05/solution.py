"""recipebook -- adapt authored recipes to a target yield, available
equipment, dietary exclusions, and a substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is
JSON-shaped.  Catalog ``result`` fields understood here:
    name                    replacement ingredient name (required)
    quantity_factor         Fraction multiplier on the replaced quantity
    unit                    replacement unit
    preparation             replacement preparation wording
    wording_changes         [{"old": str, "new": str}] applied to every
                            instruction string
    add_equipment /
    remove_equipment        equipment list edits
    additional_ingredients  extra ingredient records, themselves subject
                            to exclusions and further catalog choices
Neither public function mutates its arguments.
"""

import copy
from fractions import Fraction

__all__ = ["adapt", "print_original"]


def print_original(recipe):
    """Return the recipe exactly as authored."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    work = copy.deepcopy(recipe)
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])

    choices_applied = []
    warnings = []
    reasons = set()
    equipment = set(work.get("equipment") or ())

    # Instruction and ingredient lists from the recipe body and every
    # component share one substitution pass, so a choice's wording changes
    # and the exclusion rules reach everywhere.
    instruction_lists = [work.get("instructions") or []]
    ingredient_lists = [work.get("ingredients") or []]
    component_equipment = []
    for comp in work.get("components") or ():
        if not isinstance(comp, dict):
            continue
        if comp.get("instructions"):
            instruction_lists.append(comp["instructions"])
        if comp.get("ingredients"):
            ingredient_lists.append(comp["ingredients"])
        if comp.get("equipment"):
            component_equipment.append(comp)

    def best_choice(name):
        candidates = [(c["priority"], i, c)
                      for i, c in enumerate(catalog) if c["for"] == name]
        return min(candidates)[2] if candidates else None

    def apply_wording(old, new):
        for lines in instruction_lists:
            for i, line in enumerate(lines):
                lines[i] = line.replace(old, new)

    def resolve(ing, containing_list):
        chain = set()
        while ing["name"] in excluded:
            name = ing["name"]
            if name in chain:
                reasons.add("substitution cycle involving " + name)
                return
            chain.add(name)
            choice = best_choice(name)
            if choice is None:
                reasons.add("no substitution for " + name)
                return
            choices_applied.append(choice["id"])
            result = choice["result"]
            ing["name"] = result["name"]
            if "quantity_factor" in result:
                ing["quantity"] = ing["quantity"] * result["quantity_factor"]
            if "unit" in result:
                ing["unit"] = result["unit"]
            if "preparation" in result:
                ing["preparation"] = result["preparation"]
            for change in result.get("wording_changes") or ():
                apply_wording(change["old"], change["new"])
            for item in result.get("add_equipment") or ():
                equipment.add(item)
            for item in result.get("remove_equipment") or ():
                equipment.discard(item)
            for extra in result.get("additional_ingredients") or ():
                containing_list.append(copy.deepcopy(extra))
            if ing["name"] in excluded:
                warnings.append(
                    "substitution for %s reintroduced excluded ingredient %s"
                    % (name, ing["name"]))

    for lst in ingredient_lists:
        i = 0
        while i < len(lst):      # list may grow with additional ingredients
            resolve(lst[i], lst)
            i += 1

    for item in equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)
    for comp in component_equipment:
        for item in comp["equipment"]:
            if item not in available:
                reasons.add("equipment %s unavailable" % item)

    if reasons:
        return {"possible": False, "recipe": None,
                "choices": choices_applied,
                "warnings": sorted(warnings),
                "reasons": sorted(reasons)}

    target = Fraction(request["target_yield"])
    factor = target / Fraction(work["yield"])
    for lst in ingredient_lists:
        for ing in lst:
            ing["quantity"] = ing["quantity"] * factor
    work["yield"] = target
    work["equipment"] = sorted(equipment)
    for comp in component_equipment:
        comp["equipment"] = sorted(comp["equipment"])

    return {"possible": True, "recipe": work,
            "choices": choices_applied,
            "warnings": sorted(warnings),
            "reasons": []}


if __name__ == "__main__":
    F = Fraction

    def base():
        return {
            "title": "Pancakes",
            "yield": F(3),
            "ingredients": [
                {"id": "flour", "name": "wheat flour", "quantity": F(3, 2),
                 "unit": "cup", "preparation": "sifted"},
                {"id": "milk", "name": "milk", "quantity": F(1),
                 "unit": "cup", "preparation": "warmed"},
                {"id": "butter", "name": "butter", "quantity": F(2),
                 "unit": "tbsp", "preparation": "melted"},
            ],
            "equipment": ["griddle", "whisk"],
            "instructions": [
                "Whisk the sifted wheat flour into the warmed milk.",
                "Melt the butter on the griddle.",
            ],
            "components": [],
            "authored_text": ("Pancakes -- serves 3\n"
                              "1 1/2 cup wheat flour, sifted\n"
                              "1 cup milk, warmed\n"
                              "2 tbsp butter, melted\n"),
        }

    every = ["griddle", "whisk", "nonstick pan"]

    # 1. scaling 3 -> 5 servings with exact rational quantities
    out = adapt(base(), {"target_yield": F(5), "available_equipment": every,
                         "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == F(5)
    q = {i["id"]: i["quantity"] for i in out["recipe"]["ingredients"]}
    assert q == {"flour": F(5, 2), "milk": F(5, 3), "butter": F(10, 3)}

    # 2 & 4. chained substitution; reintroduced exclusion is detected
    cat = [
        {"id": "milk->oat", "for": "milk", "priority": 1,
         "result": {"name": "oat milk"}},
        {"id": "oat->soy", "for": "oat milk", "priority": 1,
         "result": {"name": "soy milk", "quantity_factor": F(9, 8)}},
    ]
    out = adapt(base(), {"target_yield": F(3), "available_equipment": every,
                         "excluded": ["milk", "oat milk"]}, cat)
    assert out["possible"]
    assert out["choices"] == ["milk->oat", "oat->soy"]
    milk = [i for i in out["recipe"]["ingredients"] if i["id"] == "milk"][0]
    assert milk["name"] == "soy milk" and milk["quantity"] == F(9, 8)
    assert out["warnings"] == [
        "substitution for milk reintroduced excluded ingredient oat milk"]

    # 3. substitution changing a referenced verb and the equipment set
    cat = [{"id": "butter->oil", "for": "butter", "priority": 1,
            "result": {"name": "sunflower oil", "unit": "tbsp",
                       "preparation": "",
                       "wording_changes": [
                           {"old": "Melt the butter on the griddle.",
                            "new": "Pour the sunflower oil into the "
                                   "nonstick pan."}],
                       "add_equipment": ["nonstick pan"],
                       "remove_equipment": ["griddle"]}}]
    out = adapt(base(), {"target_yield": F(3),
                         "available_equipment": ["whisk", "nonstick pan"],
                         "excluded": ["butter"]}, cat)
    assert out["possible"]
    assert out["recipe"]["instructions"][1] == \
        "Pour the sunflower oil into the nonstick pan."
    assert out["recipe"]["equipment"] == ["nonstick pan", "whisk"]

    # 5. multiple simultaneous impossibility reasons, all reported
    cat = [
        {"id": "milk->almond", "for": "milk", "priority": 1,
         "result": {"name": "almond milk"}},
        {"id": "almond->milk", "for": "almond milk", "priority": 1,
         "result": {"name": "milk"}},
    ]
    out = adapt(base(), {"target_yield": F(4),
                         "available_equipment": ["whisk"],
                         "excluded": ["milk", "almond milk", "wheat flour"]},
                cat)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == sorted([
        "equipment griddle unavailable",
        "no substitution for wheat flour",
        "substitution cycle involving milk",
    ])

    # 6. original untouched and printable byte-for-byte after adaptations
    r = base()
    frozen = copy.deepcopy(r)
    adapt(r, {"target_yield": F(7), "available_equipment": every,
              "excluded": ["butter"]},
          [{"id": "butter->oil", "for": "butter", "priority": 1,
            "result": {"name": "oil"}}])
    assert r == frozen
    assert print_original(r) == frozen["authored_text"]

    # 7. deterministic choice: lower priority wins, catalog order breaks ties
    cat = [
        {"id": "late-low", "for": "butter", "priority": 2,
         "result": {"name": "margarine"}},
        {"id": "first-high", "for": "butter", "priority": 1,
         "result": {"name": "ghee"}},
        {"id": "second-high", "for": "butter", "priority": 1,
         "result": {"name": "shortening"}},
    ]
    out = adapt(base(), {"target_yield": F(3), "available_equipment": every,
                         "excluded": ["butter"]}, cat)
    assert out["choices"] == ["first-high"]
    assert [i["name"] for i in out["recipe"]["ingredients"]][2] == "ghee"

    print("all acceptance checks passed")
