"""recipebook: adapt authored recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Catalog ``result`` fields understood here:
    name                    replacement ingredient name (required)
    quantity_factor         Fraction multiplier applied to the replaced quantity
    unit                    replacement unit
    preparation             replacement preparation text
    wording_changes         [{"old": ..., "new": ...}] applied to instructions
    yield_factor            Fraction: how the substitute scales produced yield
    equipment_add           equipment names required by the substitute
    equipment_remove        equipment names no longer needed
    additional_ingredients  ingredient dicts introduced by the substitute
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the authored text exactly as written."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    target = Fraction(request["target_yield"])
    available = set(request.get("available_equipment", ()))
    excluded = set(request.get("excluded", ()))
    scale = target / Fraction(recipe["yield"])

    # Lowest priority wins; catalog order breaks ties.
    options_for = {}
    for index, choice in enumerate(catalog):
        options_for.setdefault(choice["for"], []).append(
            (choice.get("priority", 0), index, choice))
    for ranked in options_for.values():
        ranked.sort(key=lambda entry: (entry[0], entry[1]))

    reasons = set()
    warnings = set()
    choices = []
    equipment = set(recipe.get("equipment", ()))
    instructions = list(recipe.get("instructions", ()))
    yield_comp = Fraction(1)
    tracked = []  # every live ingredient dict, for global text rewrites

    def rewrite(old, new):
        nonlocal instructions
        instructions = [line.replace(old, new) for line in instructions]
        for ing in tracked:
            prep = ing.get("preparation")
            if isinstance(prep, str):
                ing["preparation"] = prep.replace(old, new)

    queue = []  # (working ingredient dict, destination list)
    main_ingredients = []
    for ing in recipe.get("ingredients", ()):
        queue.append((copy.deepcopy(ing), main_ingredients))

    components = []
    for comp in recipe.get("components", ()):
        comp = copy.deepcopy(comp)
        if isinstance(comp, dict) and isinstance(comp.get("ingredients"), list):
            authored = comp["ingredients"]
            comp["ingredients"] = []
            for ing in authored:
                queue.append((ing, comp["ingredients"]))
        components.append(comp)

    def apply_substitutions(ing):
        nonlocal yield_comp
        original_name = ing["name"]
        seen = {original_name}
        steps = 0
        # seen grows every iteration and result names are finite, so this
        # loop always terminates in a substitution, a cycle, or a dead end
        while ing["name"] in excluded:
            ranked = options_for.get(ing["name"])
            if not ranked:
                reasons.add("no substitution for %s" % ing["name"])
                return
            choice = ranked[0][2]
            result = choice["result"]
            new_name = result["name"]
            choices.append(choice["id"])
            if new_name in seen:
                reasons.add("substitution cycle involving %s" % new_name)
                return
            seen.add(new_name)
            if steps:
                warnings.add("chained substitution for %s" % original_name)
            steps += 1
            old_name = ing["name"]
            ing["name"] = new_name
            if "quantity_factor" in result:
                ing["quantity"] = ing["quantity"] * Fraction(result["quantity_factor"])
            if "unit" in result:
                if result["unit"] != ing.get("unit"):
                    warnings.add("unit changed for %s" % new_name)
                ing["unit"] = result["unit"]
            if "preparation" in result:
                ing["preparation"] = result["preparation"]
            for change in result.get("wording_changes", ()):
                rewrite(change["old"], change["new"])
            rewrite(old_name, new_name)  # excluded name may remain nowhere
            for item in result.get("equipment_add", ()):
                if item not in equipment:
                    warnings.add("equipment added: %s" % item)
                equipment.add(item)
            for item in result.get("equipment_remove", ()):
                if item in equipment:
                    warnings.add("equipment removed: %s" % item)
                equipment.discard(item)
            if "yield_factor" in result:
                yield_comp *= Fraction(result["yield_factor"])
                warnings.add("yield adjusted by %s" % choice["id"])
            for extra in result.get("additional_ingredients", ()):
                extra = copy.deepcopy(extra)
                extra.setdefault("id", extra["name"])
                extra.setdefault("unit", None)
                extra.setdefault("preparation", "")
                warnings.add("added ingredient %s" % extra["name"])
                if extra["name"] in excluded:
                    warnings.add(
                        "excluded ingredient %s reintroduced" % extra["name"])
                queue.append((extra, main_ingredients))

    cursor = 0
    while cursor < len(queue):
        ing, sink = queue[cursor]
        cursor += 1
        ing["quantity"] = Fraction(ing["quantity"]) * scale
        tracked.append(ing)
        apply_substitutions(ing)
        sink.append(ing)

    for item in equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)

    if reasons:
        return {"possible": False, "recipe": None, "choices": choices,
                "warnings": sorted(warnings), "reasons": sorted(reasons)}

    if yield_comp != 1:
        # a substitute changes the produced yield; rescale quantities so the
        # adapted recipe still lands exactly on the requested target yield
        for ing in tracked:
            ing["quantity"] = ing["quantity"] / yield_comp

    adapted = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": main_ingredients,
        "equipment": sorted(equipment),
        "instructions": instructions,
        "components": components,
        "authored_text": recipe["authored_text"],
    }
    return {"possible": True, "recipe": adapted, "choices": choices,
            "warnings": sorted(warnings), "reasons": []}


if __name__ == "__main__":
    F = Fraction

    recipe = {
        "title": "Herb Cake",
        "yield": F(3),
        "ingredients": [
            {"id": "i1", "name": "butter", "quantity": F(1, 2), "unit": "cup",
             "preparation": "softened"},
            {"id": "i2", "name": "flour", "quantity": F(3), "unit": "cup",
             "preparation": "sifted"},
        ],
        "equipment": ["mixer", "pan"],
        "instructions": ["cream the butter", "fold in the flour"],
        "components": [],
        "authored_text": ("Herb Cake\n1/2 cup butter, softened\n"
                          "3 cup flour, sifted\ncream the butter\n"
                          "fold in the flour\nServes 3.\n"),
    }
    catalog = [
        {"id": "c-marg", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "c-oil", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "quantity_factor": F(3, 4),
                    "unit": "cup", "preparation": "",
                    "wording_changes": [{"old": "cream the",
                                         "new": "whisk the"}],
                    "equipment_add": ["whisk"],
                    "equipment_remove": ["mixer"]}},
        {"id": "c-oil-low", "for": "butter", "priority": 2,
         "result": {"name": "olive oil"}},
    ]
    request = {"target_yield": F(5),
               "available_equipment": ["pan", "whisk", "oven"],
               "excluded": ["butter", "margarine"]}

    before = copy.deepcopy(recipe)
    out = adapt(recipe, request, catalog)
    assert out["possible"] and out["reasons"] == []

    # scaling 3 -> 5 servings, plus chained replacement quantity factor
    oil = out["recipe"]["ingredients"][0]
    assert oil["name"] == "olive oil"
    assert oil["quantity"] == F(1, 2) * F(5, 3) * F(3, 4)
    assert out["recipe"]["ingredients"][1]["quantity"] == F(5)
    assert out["recipe"]["yield"] == F(5)

    # deterministic priority: c-marg (priority 1) beats c-oil-low (priority 2),
    # then the chain continues through c-oil
    assert out["choices"] == ["c-marg", "c-oil"]
    assert "chained substitution for butter" in out["warnings"]

    # substitution changed the referenced verb and the equipment
    assert out["recipe"]["instructions"][0] == "whisk the olive oil"
    assert out["recipe"]["equipment"] == ["pan", "whisk"]

    # originals untouched and printable byte-for-byte after adaptation
    assert recipe == before
    assert print_original(recipe) == before["authored_text"]

    # an additional ingredient reintroduces an exclusion and is re-adapted
    catalog2 = [
        {"id": "c-honey", "for": "sugar", "priority": 1,
         "result": {"name": "honey",
                    "additional_ingredients": [
                        {"id": "x1", "name": "butter", "quantity": F(1, 4),
                         "unit": "cup", "preparation": ""}]}},
        {"id": "c-marg2", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
    ]
    recipe2 = {
        "title": "Glaze", "yield": F(2),
        "ingredients": [{"id": "s", "name": "sugar", "quantity": F(1),
                         "unit": "cup", "preparation": ""}],
        "equipment": [], "instructions": ["stir in the sugar"],
        "components": [], "authored_text": "Glaze\n",
    }
    out2 = adapt(recipe2, {"target_yield": F(2), "available_equipment": [],
                           "excluded": ["sugar", "butter"]}, catalog2)
    assert out2["possible"]
    assert [i["name"] for i in out2["recipe"]["ingredients"]] == \
        ["honey", "margarine"]
    assert out2["recipe"]["instructions"] == ["stir in the honey"]
    assert "excluded ingredient butter reintroduced" in out2["warnings"]

    # every independently applicable impossibility reason is reported
    recipe3 = {
        "title": "X", "yield": F(2),
        "ingredients": [
            {"id": "a", "name": "sugar", "quantity": F(1), "unit": "cup",
             "preparation": ""},
            {"id": "b", "name": "butter", "quantity": F(1), "unit": "cup",
             "preparation": ""}],
        "equipment": ["torch"], "instructions": [], "components": [],
        "authored_text": "X\n"}
    out3 = adapt(recipe3, {"target_yield": F(2), "available_equipment": [],
                           "excluded": ["sugar", "butter"]}, [])
    assert out3["possible"] is False and out3["recipe"] is None
    assert out3["reasons"] == ["equipment torch unavailable",
                               "no substitution for butter",
                               "no substitution for sugar"]

    # substitution cycle detection
    cyc = [
        {"id": "ab", "for": "aioli", "priority": 1,
         "result": {"name": "butter"}},
        {"id": "ba", "for": "butter", "priority": 1,
         "result": {"name": "aioli"}},
    ]
    recipe4 = {"title": "Y", "yield": F(1),
               "ingredients": [{"id": "a", "name": "aioli", "quantity": F(1),
                                "unit": "cup", "preparation": ""}],
               "equipment": [], "instructions": [], "components": [],
               "authored_text": "Y\n"}
    out4 = adapt(recipe4, {"target_yield": F(1), "available_equipment": [],
                           "excluded": ["aioli", "butter"]}, cyc)
    assert out4["possible"] is False
    assert out4["reasons"] == ["substitution cycle involving aioli"]

    print("all self-checks passed")
