"""recipebook.py — adapt an authored recipe to a target yield, available
equipment, dietary exclusions, and a prioritized substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Uses only the Python standard library.
"""

from fractions import Fraction
import copy

# Accepted spellings for the loosely specified optional result keys.
_EQUIP_ADD_KEYS = ("equipment_add", "add_equipment", "equipment_additions")
_EQUIP_REMOVE_KEYS = ("equipment_remove", "remove_equipment", "equipment_removals")
_EXTRA_ING_KEYS = ("additional_ingredients", "add_ingredients", "additional")


def print_original(recipe):
    """Return the authored text byte-for-byte; never touches the recipe."""
    return recipe["authored_text"]


def _get_any(mapping, keys, default):
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _best_choice(catalog, name):
    """Lowest numeric priority wins; catalog order breaks ties."""
    best_key = best = None
    for index, choice in enumerate(catalog):
        if choice["for"] != name:
            continue
        key = (choice["priority"], index)
        if best_key is None or key < best_key:
            best_key, best = key, choice
    return best


def _ingredient_lists(recipe):
    yield recipe["ingredients"]
    for component in recipe.get("components") or []:
        if isinstance(component, dict) and isinstance(component.get("ingredients"), list):
            yield component["ingredients"]


def _instruction_lists(recipe):
    yield recipe["instructions"]
    for component in recipe.get("components") or []:
        if isinstance(component, dict) and isinstance(component.get("instructions"), list):
            yield component["instructions"]


def adapt(recipe, request, catalog):
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])
    target_yield = Fraction(request["target_yield"])

    adapted = copy.deepcopy(recipe)  # inputs are never mutated
    scale = target_yield / Fraction(recipe["yield"])
    adapted["yield"] = target_yield
    for ingredients in _ingredient_lists(adapted):
        for ingredient in ingredients:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    choices = []
    warnings = set()
    reasons = set()
    equipment = set(adapted.get("equipment") or [])

    def rewrite_instructions(pairs):
        for pair in pairs:
            for instructions in _instruction_lists(adapted):
                for i, line in enumerate(instructions):
                    instructions[i] = line.replace(pair["old"], pair["new"])

    for ingredients in _ingredient_lists(adapted):
        i = 0
        while i < len(ingredients):
            ingredient = ingredients[i]
            seen = {ingredient["name"]}
            while ingredient["name"] in excluded:
                name = ingredient["name"]
                choice = _best_choice(catalog, name)
                if choice is None:
                    reasons.add("no substitution for " + name)
                    break
                choices.append(choice["id"])
                result = choice["result"]
                replacement = result["name"]
                if replacement in seen:
                    reasons.add("substitution cycle involving " + replacement)
                    break
                seen.add(replacement)

                ingredient["name"] = replacement
                if "quantity_factor" in result:
                    ingredient["quantity"] *= Fraction(result["quantity_factor"])
                if "unit" in result:
                    ingredient["unit"] = result["unit"]
                if "preparation" in result:
                    ingredient["preparation"] = result["preparation"]
                rewrite_instructions(result.get("wording_changes") or [])
                equipment.update(_get_any(result, _EQUIP_ADD_KEYS, ()))
                equipment.difference_update(_get_any(result, _EQUIP_REMOVE_KEYS, ()))

                if "yield_factor" in result and Fraction(result["yield_factor"]) != 1:
                    # A substitution that shifts the natural yield is compensated
                    # so the recipe stays at the requested target yield.
                    correction = 1 / Fraction(result["yield_factor"])
                    for lst in _ingredient_lists(adapted):
                        for other in lst:
                            other["quantity"] *= correction
                    scale *= correction
                    warnings.add(
                        "quantities rescaled to hold target yield after " + choice["id"])

                for extra in _get_any(result, _EXTRA_ING_KEYS, ()):
                    extra = copy.deepcopy(extra)
                    extra.setdefault("id", extra["name"])
                    extra.setdefault("unit", "")
                    extra.setdefault("preparation", "")
                    extra["quantity"] = Fraction(extra.get("quantity", 1)) * scale
                    ingredients.append(extra)  # re-enters the same pipeline
                    if extra["name"] in excluded:
                        warnings.add("substitution %s introduced excluded ingredient %s"
                                     % (choice["id"], extra["name"]))

                if replacement in excluded:
                    warnings.add("substitution %s introduced excluded ingredient %s"
                                 % (choice["id"], replacement))
            i += 1

    adapted["equipment"] = sorted(equipment)
    for item in adapted["equipment"]:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)
    for component in adapted.get("components") or []:
        if isinstance(component, dict) and isinstance(component.get("equipment"), list):
            component["equipment"].sort()
            for item in component["equipment"]:
                if item not in available:
                    reasons.add("equipment %s unavailable" % item)

    return {
        "possible": not reasons,
        "recipe": None if reasons else adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted(reasons),
    }


if __name__ == "__main__":
    F = Fraction

    recipe = {
        "title": "Pancakes",
        "yield": F(3),
        "ingredients": [
            {"id": "flour", "name": "wheat flour", "quantity": F(3, 2),
             "unit": "cup", "preparation": "sifted"},
            {"id": "milk", "name": "cow milk", "quantity": F(1),
             "unit": "cup", "preparation": "warmed"},
            {"id": "butter", "name": "butter", "quantity": F(2),
             "unit": "tbsp", "preparation": "melted"},
        ],
        "equipment": ["whisk", "skillet"],
        "instructions": [
            "Whisk the warmed cow milk into the sifted wheat flour.",
            "Fry in melted butter on the skillet.",
        ],
        "components": [],
        "authored_text": "Pancakes\nServes 3.\nWhisk milk into flour; fry in butter.\n",
    }
    snapshot = copy.deepcopy(recipe)

    catalog = [
        {"id": "milk->oat", "for": "cow milk", "priority": 2,
         "result": {"name": "oat milk"}},
        {"id": "milk->soy", "for": "cow milk", "priority": 1,
         "result": {"name": "soy milk",
                    "wording_changes": [{"old": "warmed cow milk",
                                         "new": "room-temperature soy milk"}]}},
        {"id": "butter->ghee", "for": "butter", "priority": 1,
         "result": {"name": "ghee"}},
        {"id": "ghee->oil", "for": "ghee", "priority": 1,
         "result": {"name": "olive oil", "quantity_factor": F(3, 4),
                    "unit": "tbsp", "preparation": "",
                    "wording_changes": [
                        {"old": "Fry in melted butter on the skillet",
                         "new": "Sear in olive oil on the griddle"}],
                    "equipment_remove": ["skillet"],
                    "equipment_add": ["griddle"]}},
    ]

    out = adapt(recipe,
                {"target_yield": F(5),
                 "available_equipment": ["whisk", "griddle", "bowl"],
                 "excluded": ["cow milk", "butter", "ghee"]},
                catalog)
    assert out["possible"] and out["reasons"] == []
    by_id = {ing["id"]: ing for ing in out["recipe"]["ingredients"]}
    # scaling 3 -> 5 servings
    assert out["recipe"]["yield"] == F(5)
    assert by_id["flour"]["quantity"] == F(5, 2)
    assert by_id["milk"]["quantity"] == F(5, 3)
    # chained substitution: butter -> ghee -> olive oil with factor 3/4
    assert by_id["butter"]["name"] == "olive oil"
    assert by_id["butter"]["quantity"] == F(2) * F(5, 3) * F(3, 4)
    assert by_id["butter"]["unit"] == "tbsp"
    # deterministic priority: soy (priority 1) beats oat (priority 2)
    assert by_id["milk"]["name"] == "soy milk"
    assert out["choices"] == ["milk->soy", "butter->ghee", "ghee->oil"]
    # substitution changed a referenced verb and the equipment
    assert out["recipe"]["instructions"][0] == \
        "Whisk the room-temperature soy milk into the sifted wheat flour."
    assert out["recipe"]["instructions"][1] == "Sear in olive oil on the griddle."
    assert out["recipe"]["equipment"] == ["griddle", "whisk"]
    # reintroduced exclusion (ghee) was detected, warned about, and resolved
    assert any("ghee" in w for w in out["warnings"])
    assert out["warnings"] == sorted(out["warnings"])
    # exact original print after adaptation; inputs unchanged
    assert print_original(recipe) == snapshot["authored_text"]
    assert recipe == snapshot

    bad = adapt(recipe,
                {"target_yield": F(5), "available_equipment": ["skillet"],
                 "excluded": ["wheat flour", "cow milk", "almond milk"]},
                [{"id": "milk->almond", "for": "cow milk", "priority": 1,
                  "result": {"name": "almond milk"}},
                 {"id": "almond->milk", "for": "almond milk", "priority": 1,
                  "result": {"name": "cow milk"}}])
    assert not bad["possible"] and bad["recipe"] is None
    # every independently applicable reason, sorted
    assert bad["reasons"] == [
        "equipment whisk unavailable",
        "no substitution for wheat flour",
        "substitution cycle involving cow milk",
    ]
    assert recipe == snapshot
    print("all acceptance checks passed")
