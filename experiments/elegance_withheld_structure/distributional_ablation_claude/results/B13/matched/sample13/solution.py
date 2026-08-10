"""recipebook.py -- adapt structured recipes to yield, equipment, and diet.

Public API: adapt(recipe, request, catalog) -> dict, print_original(recipe) -> str.
Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Standard library only.
"""

import copy
from fractions import Fraction


def print_original(recipe):
    """Return the authored text byte-for-byte."""
    return recipe["authored_text"]


def _catalog_index(catalog):
    """Map ingredient name -> choices sorted by (priority, catalog order)."""
    by_name = {}
    for order, choice in enumerate(catalog):
        by_name.setdefault(choice["for"], []).append((choice["priority"], order, choice))
    for options in by_name.values():
        options.sort(key=lambda entry: (entry[0], entry[1]))
    return by_name


def adapt(recipe, request, catalog):
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / Fraction(recipe["yield"])
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])
    by_name = _catalog_index(catalog)

    reasons = set()
    warnings = set()
    choices = []
    pending_wording = []
    equipment = set(recipe["equipment"])
    yield_drift = Fraction(1)

    def substitute(ing):
        """Adapt one ingredient, chaining catalog choices while it is excluded."""
        nonlocal yield_drift
        adapted = {
            "id": ing.get("id"),
            "name": ing["name"],
            "quantity": Fraction(ing["quantity"]) * scale,
            "unit": ing.get("unit"),
            "preparation": ing.get("preparation", ""),
        }
        extras = []
        seen = {adapted["name"]}
        while adapted["name"] in excluded:
            options = by_name.get(adapted["name"])
            if not options:
                reasons.add("no substitution for %s" % adapted["name"])
                break
            choice = options[0][2]
            choices.append(choice["id"])
            result = choice["result"]
            adapted["quantity"] *= Fraction(result.get("quantity_factor", 1))
            adapted["unit"] = result.get("unit", adapted["unit"])
            adapted["preparation"] = result.get("preparation", adapted["preparation"])
            pending_wording.extend(result.get("wording_changes", []))
            equipment.difference_update(result.get("remove_equipment", []))
            equipment.update(result.get("add_equipment", []))
            factor = Fraction(result.get("yield_factor", 1))
            if factor != 1:
                yield_drift *= factor
                warnings.add(
                    "substitution %s altered yield; quantities rescaled" % choice["id"])
            extras.extend(copy.deepcopy(result.get("additional_ingredients", [])))
            new_name = result["name"]
            if new_name in seen:
                reasons.add("substitution cycle involving %s" % new_name)
                break
            if new_name in excluded:
                warnings.add(
                    "substitution for %s reintroduced excluded %s"
                    % (adapted["name"], new_name))
            seen.add(new_name)
            adapted["name"] = new_name
        return adapted, extras

    def process(ingredients):
        """Adapt a list of ingredients; introduced ingredients join the queue."""
        out = []
        queue = list(ingredients)
        while queue:
            adapted, extras = substitute(queue.pop(0))
            out.append(adapted)
            queue.extend(extras)
        return out

    main_ingredients = process(recipe["ingredients"])
    components = []
    for comp in recipe.get("components", []):
        new_comp = {key: copy.deepcopy(value) for key, value in comp.items()
                    if key not in ("ingredients", "instructions")}
        new_comp["ingredients"] = process(comp.get("ingredients", []))
        new_comp["instructions"] = list(comp.get("instructions", []))
        components.append(new_comp)

    instructions = list(recipe["instructions"])
    for change in pending_wording:
        old, new = change["old"], change["new"]
        hit = False

        def swap(strings):
            nonlocal hit
            out = []
            for text in strings:
                if old in text:
                    hit = True
                    text = text.replace(old, new)
                out.append(text)
            return out

        instructions = swap(instructions)
        for comp in components:
            comp["instructions"] = swap(comp["instructions"])
        if not hit:
            warnings.add("wording change '%s' had no effect" % old)

    if yield_drift != 1:
        # A substitution changed how much the recipe makes; rescale so the
        # adapted recipe still lands exactly on the requested yield.
        for ing in main_ingredients + [i for c in components for i in c["ingredients"]]:
            ing["quantity"] /= yield_drift

    for item in equipment - available:
        reasons.add("equipment %s unavailable" % item)

    possible = not reasons
    adapted_recipe = None
    if possible:
        adapted_recipe = {
            "title": recipe["title"],
            "yield": target_yield,
            "ingredients": main_ingredients,
            "equipment": sorted(equipment),
            "instructions": instructions,
            "components": components,
            "authored_text": recipe["authored_text"],
        }
    return {
        "possible": possible,
        "recipe": adapted_recipe,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }


def _sample_recipe():
    return {
        "title": "Pancakes",
        "yield": Fraction(3),
        "ingredients": [
            {"id": "flour", "name": "flour", "quantity": Fraction(3, 2),
             "unit": "cup", "preparation": "sifted"},
            {"id": "butter", "name": "butter", "quantity": Fraction(3),
             "unit": "tbsp", "preparation": "softened"},
            {"id": "milk", "name": "milk", "quantity": Fraction(3, 4),
             "unit": "cup", "preparation": ""},
        ],
        "equipment": ["mixer", "skillet"],
        "instructions": [
            "Cream the butter in the mixer.",
            "Whisk in the milk and flour.",
            "Fry ladlefuls in the skillet.",
        ],
        "components": [
            {"name": "berry topping", "optional": True,
             "ingredients": [
                 {"id": "cream", "name": "cream", "quantity": Fraction(1, 2),
                  "unit": "cup", "preparation": "whipped"}],
             "instructions": ["Fold berries into the cream."]},
        ],
        "authored_text": (
            "Pancakes\nServes 3\n"
            "1 1/2 cup flour, sifted; 3 tbsp butter, softened; 3/4 cup milk\n"
            "Cream the butter in the mixer. Whisk in the milk and flour. "
            "Fry ladlefuls in the skillet.\n"
            "Optional berry topping: 1/2 cup cream, whipped."),
    }


def _run_acceptance():
    # 1. Scaling 3 -> 5 servings.
    request = {"target_yield": Fraction(5),
               "available_equipment": ["mixer", "skillet"], "excluded": []}
    out = adapt(_sample_recipe(), request, [])
    assert out["possible"] and out["reasons"] == [] and out["choices"] == []
    assert out["recipe"]["yield"] == Fraction(5)
    got = {i["name"]: i["quantity"] for i in out["recipe"]["ingredients"]}
    assert got == {"flour": Fraction(5, 2), "butter": Fraction(5),
                   "milk": Fraction(5, 4)}
    assert out["recipe"]["components"][0]["ingredients"][0]["quantity"] == Fraction(5, 6)

    # 2. Chained substitution: butter -> margarine -> oil.
    catalog = [
        {"id": "butter->margarine", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "margarine->oil", "for": "margarine", "priority": 1,
         "result": {"name": "oil", "quantity_factor": Fraction(3, 4), "unit": "tbsp"}},
    ]
    request = {"target_yield": Fraction(3),
               "available_equipment": ["mixer", "skillet"],
               "excluded": ["butter", "margarine"]}
    out = adapt(_sample_recipe(), request, catalog)
    assert out["possible"]
    assert out["choices"] == ["butter->margarine", "margarine->oil"]
    fat = out["recipe"]["ingredients"][1]
    assert (fat["name"], fat["quantity"], fat["unit"]) == ("oil", Fraction(9, 4), "tbsp")
    assert out["warnings"] == ["substitution for butter reintroduced excluded margarine"]

    # 3. Substitution changes a referenced verb and the equipment list.
    catalog = [
        {"id": "butter->oil", "for": "butter", "priority": 1,
         "result": {"name": "oil", "preparation": "",
                    "wording_changes": [
                        {"old": "Cream the butter in the mixer.",
                         "new": "Whisk the oil in a bowl."}],
                    "remove_equipment": ["mixer"], "add_equipment": ["whisk"]}},
    ]
    request = {"target_yield": Fraction(3),
               "available_equipment": ["skillet", "whisk"], "excluded": ["butter"]}
    out = adapt(_sample_recipe(), request, catalog)
    assert out["possible"]
    assert out["recipe"]["instructions"][0] == "Whisk the oil in a bowl."
    assert out["recipe"]["equipment"] == ["skillet", "whisk"]

    # 4. A substitution reintroduces an excluded ingredient with no way out.
    catalog = [
        {"id": "milk->oat", "for": "milk", "priority": 1,
         "result": {"name": "oat milk",
                    "additional_ingredients": [
                        {"id": "lecithin", "name": "soy lecithin",
                         "quantity": Fraction(1, 2), "unit": "tsp",
                         "preparation": ""}]}},
    ]
    request = {"target_yield": Fraction(3),
               "available_equipment": ["mixer", "skillet"],
               "excluded": ["milk", "soy lecithin"]}
    out = adapt(_sample_recipe(), request, catalog)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["no substitution for soy lecithin"]
    assert out["choices"] == ["milk->oat"]

    # 5. Every independent impossibility reason is reported together.
    catalog = [
        {"id": "milk->cream", "for": "milk", "priority": 1,
         "result": {"name": "cream"}},
        {"id": "cream->milk", "for": "cream", "priority": 1,
         "result": {"name": "milk"}},
    ]
    request = {"target_yield": Fraction(3),
               "available_equipment": ["skillet"],
               "excluded": ["butter", "milk", "cream"]}
    out = adapt(_sample_recipe(), request, catalog)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == [
        "equipment mixer unavailable",
        "no substitution for butter",
        "substitution cycle involving cream",
        "substitution cycle involving milk",
    ]

    # 6. Originals stay printable exactly as authored; inputs are unmutated.
    recipe = _sample_recipe()
    frozen = copy.deepcopy(recipe)
    catalog = [
        {"id": "butter->oil", "for": "butter", "priority": 1,
         "result": {"name": "oil",
                    "wording_changes": [
                        {"old": "Cream the butter in the mixer.",
                         "new": "Whisk the oil in a bowl."}],
                    "remove_equipment": ["mixer"], "add_equipment": ["whisk"]}},
    ]
    request = {"target_yield": Fraction(5),
               "available_equipment": ["skillet", "whisk"], "excluded": ["butter"]}
    frozen_request = copy.deepcopy(request)
    frozen_catalog = copy.deepcopy(catalog)
    out = adapt(recipe, request, catalog)
    assert out["possible"]
    assert recipe == frozen and request == frozen_request and catalog == frozen_catalog
    assert print_original(recipe) == frozen["authored_text"]

    # 7. Deterministic choice: lowest priority wins, catalog order breaks ties.
    catalog = [
        {"id": "b-late", "for": "butter", "priority": 5,
         "result": {"name": "shortening"}},
        {"id": "b-first", "for": "butter", "priority": 1, "result": {"name": "oil"}},
        {"id": "b-second", "for": "butter", "priority": 1, "result": {"name": "lard"}},
    ]
    request = {"target_yield": Fraction(3),
               "available_equipment": ["mixer", "skillet"], "excluded": ["butter"]}
    out = adapt(_sample_recipe(), request, catalog)
    assert out["possible"] and out["choices"] == ["b-first"]
    assert out["recipe"]["ingredients"][1]["name"] == "oil"

    print("all acceptance checks passed")


if __name__ == "__main__":
    _run_acceptance()
