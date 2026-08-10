"""recipebook: yield scaling and substitution-driven recipe adaptation.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Data shapes (quantities and yields are fractions.Fraction; the rest is
JSON-shaped):
    recipe:     {title, yield, ingredients, equipment, instructions,
                 components, authored_text}
    ingredient: {id, name, quantity, unit, preparation}
    component:  {name, optional, ingredients, equipment, instructions}
    request:    {target_yield, available_equipment, excluded}
    choice:     {id, for, priority, result}
    result:     {name, quantity_factor?, unit?, preparation?,
                 wording_changes? [{old, new}, ...],
                 add_equipment? [...], remove_equipment? [...],
                 additional_ingredients? [{name, quantity?, unit?,
                                           preparation?, id?}, ...],
                 yield_factor?}

Lower numeric priority wins; catalog order breaks ties.  Neither public
function mutates its inputs.
"""

from fractions import Fraction
import copy

_MAX_QUEUE_STEPS = 1000


def print_original(recipe):
    """Return the recipe exactly as authored, byte for byte."""
    return recipe["authored_text"]


def _slug(name):
    return name.lower().replace(" ", "_")


def _pick_choice(name, catalog):
    best = None
    best_key = None
    for index, choice in enumerate(catalog):
        if choice["for"] != name:
            continue
        key = (choice["priority"], index)
        if best is None or key < best_key:
            best, best_key = choice, key
    return best


def _resolve_ingredient(ingredient, excluded, catalog,
                        choices, reasons, warnings, effects):
    """Follow the substitution chain for one ingredient.

    Returns the adapted ingredient (quantities not yet scaled to the target
    yield) or None if no acceptable replacement exists; failure reasons are
    added to `reasons` and side effects of applied choices to `effects`.
    """
    name = ingredient["name"]
    original_name = name
    quantity = ingredient["quantity"]
    unit = ingredient.get("unit")
    preparation = ingredient.get("preparation")
    seen = {name}
    while name in excluded:
        choice = _pick_choice(name, catalog)
        if choice is None:
            reasons.add("no substitution for %s" % name)
            return None
        choices.append(choice["id"])
        result = choice["result"]
        quantity = quantity * Fraction(result.get("quantity_factor", 1))
        if "unit" in result and result["unit"] != unit:
            warnings.add("unit of %s changed from %s to %s"
                         % (original_name, unit, result["unit"]))
            unit = result["unit"]
        if "preparation" in result:
            preparation = result["preparation"]
        for pair in result.get("wording_changes", []):
            effects["wording_changes"].append(
                {"old": pair["old"], "new": pair["new"]})
        for item in result.get("add_equipment", []):
            effects["add_equipment"].append(item)
            warnings.add("equipment %s added by substitution %s"
                         % (item, choice["id"]))
        for item in result.get("remove_equipment", []):
            effects["remove_equipment"].append(item)
            warnings.add("equipment %s no longer needed after substitution %s"
                         % (item, choice["id"]))
        for extra in result.get("additional_ingredients", []):
            effects["additional"].append(extra)
        if "yield_factor" in result:
            effects["yield_factors"].append(Fraction(result["yield_factor"]))
            warnings.add("yield rescaled by substitution %s" % choice["id"])
        name = result["name"]
        if name in seen:
            reasons.add("substitution cycle involving %s" % name)
            return None
        seen.add(name)
    return {
        "id": ingredient.get("id", _slug(name)),
        "name": name,
        "quantity": quantity,
        "unit": unit,
        "preparation": preparation,
    }


def adapt(recipe, request, catalog):
    """Adapt `recipe` to the request's yield, equipment, and exclusions.

    Returns {possible, recipe, choices, warnings, reasons}.  On success the
    recipe is complete at the target yield with no excluded ingredient
    anywhere; on failure recipe is None and reasons lists every independently
    applicable reason, sorted.
    """
    excluded = set(request.get("excluded", []))
    available = set(request.get("available_equipment", []))
    target_yield = Fraction(request["target_yield"])

    reasons = set()
    warnings = set()
    choices = []
    effects = {"wording_changes": [], "add_equipment": [],
               "remove_equipment": [], "additional": [], "yield_factors": []}

    def process_list(raw_ingredients):
        adapted = []
        pending = [dict(raw) for raw in raw_ingredients]
        steps = 0
        while pending:
            steps += 1
            raw = pending.pop(0)
            if steps > _MAX_QUEUE_STEPS:
                reasons.add("substitution cycle involving %s" % raw["name"])
                break
            mark = len(effects["additional"])
            resolved = _resolve_ingredient(raw, excluded, catalog,
                                           choices, reasons, warnings, effects)
            extras = effects["additional"][mark:]
            del effects["additional"][mark:]
            if resolved is not None:
                adapted.append(resolved)
            for extra in extras:
                pending.append({
                    "id": extra.get("id", _slug(extra["name"])),
                    "name": extra["name"],
                    "quantity": Fraction(extra.get("quantity", 1)),
                    "unit": extra.get("unit"),
                    "preparation": extra.get("preparation"),
                })
        return adapted

    main_ingredients = process_list(recipe["ingredients"])
    component_ingredients = [process_list(component.get("ingredients", []))
                             for component in recipe.get("components", [])]

    removed = set(effects["remove_equipment"])
    equipment = ((set(recipe.get("equipment", []))
                  | set(effects["add_equipment"])) - removed)
    needed = set(equipment)
    for component in recipe.get("components", []):
        needed |= set(component.get("equipment", [])) - removed
    for item in sorted(needed - available):
        reasons.add("equipment %s unavailable" % item)

    if reasons:
        return {"possible": False, "recipe": None, "choices": choices,
                "warnings": sorted(warnings), "reasons": sorted(reasons)}

    scale = target_yield / Fraction(recipe["yield"])
    for factor in effects["yield_factors"]:
        scale = scale / factor

    def scaled(ingredient):
        out = dict(ingredient)
        out["quantity"] = ingredient["quantity"] * scale
        return out

    def rewrite(text):
        for pair in effects["wording_changes"]:
            text = text.replace(pair["old"], pair["new"])
        return text

    components = []
    for component, resolved in zip(recipe.get("components", []),
                                   component_ingredients):
        components.append({
            "name": component.get("name"),
            "optional": component.get("optional", False),
            "ingredients": [scaled(i) for i in resolved],
            "equipment": sorted(set(component.get("equipment", [])) - removed),
            "instructions": [rewrite(step)
                             for step in component.get("instructions", [])],
        })

    adapted = {
        "title": recipe["title"],
        "yield": target_yield,
        "ingredients": [scaled(i) for i in main_ingredients],
        "equipment": sorted(equipment),
        "instructions": [rewrite(step)
                         for step in recipe.get("instructions", [])],
        "components": components,
    }
    adapted["authored_text"] = _render(adapted)
    return {"possible": True, "recipe": adapted, "choices": choices,
            "warnings": sorted(warnings), "reasons": []}


def _describe(ingredient):
    parts = [str(ingredient["quantity"])]
    if ingredient.get("unit"):
        parts.append(ingredient["unit"])
    parts.append(ingredient["name"])
    text = " ".join(parts)
    if ingredient.get("preparation"):
        text += ", %s" % ingredient["preparation"]
    return text


def _render(adapted):
    lines = [adapted["title"], "Yield: %s" % adapted["yield"], "",
             "Ingredients:"]
    for ingredient in adapted["ingredients"]:
        lines.append("  %s" % _describe(ingredient))
    if adapted["equipment"]:
        lines.append("")
        lines.append("Equipment: %s" % ", ".join(adapted["equipment"]))
    lines.append("")
    lines.append("Instructions:")
    for number, step in enumerate(adapted["instructions"], 1):
        lines.append("  %d. %s" % (number, step))
    for component in adapted["components"]:
        lines.append("")
        label = component.get("name") or "component"
        if component.get("optional"):
            label += " (optional)"
        lines.append("%s:" % label)
        for ingredient in component["ingredients"]:
            lines.append("  %s" % _describe(ingredient))
        for step in component["instructions"]:
            lines.append("  - %s" % step)
    return "\n".join(lines)


def _sample_recipe():
    return {
        "title": "Herbed Butter Pasta",
        "yield": Fraction(3),
        "ingredients": [
            {"id": "pasta", "name": "pasta", "quantity": Fraction(300),
             "unit": "g", "preparation": "boiled"},
            {"id": "butter", "name": "butter", "quantity": Fraction(3, 2),
             "unit": "tbsp", "preparation": "melted"},
            {"id": "cream", "name": "cream", "quantity": Fraction(1, 2),
             "unit": "cup", "preparation": "whipped"},
        ],
        "equipment": ["pot", "whisk"],
        "instructions": [
            "Boil the pasta in the pot.",
            "Whip the cream with the whisk.",
            "Melt the butter and toss everything together.",
        ],
        "components": [
            {"name": "garnish", "optional": True,
             "ingredients": [
                 {"id": "parsley", "name": "parsley",
                  "quantity": Fraction(1), "unit": "tbsp",
                  "preparation": "chopped"}],
             "equipment": [],
             "instructions": ["Sprinkle the chopped parsley on top."]},
        ],
        "authored_text": ("Herbed Butter Pasta\nServes 3\n\n"
                          "300 g pasta, boiled\n1 1/2 tbsp butter, melted\n"
                          "1/2 cup cream, whipped\n\n"
                          "Boil, whip, melt, toss.\n"),
    }


def _sample_catalog():
    return [
        {"id": "sub-margarine", "for": "butter", "priority": 2,
         "result": {"name": "margarine"}},
        {"id": "sub-olive-oil", "for": "butter", "priority": 1,
         "result": {"name": "olive oil",
                    "quantity_factor": Fraction(3, 4),
                    "preparation": "warmed",
                    "wording_changes": [{"old": "Melt the butter",
                                         "new": "Warm the olive oil"}]}},
        {"id": "sub-coconut-cream", "for": "cream", "priority": 1,
         "result": {"name": "coconut cream"}},
        {"id": "sub-oat-cream", "for": "coconut cream", "priority": 1,
         "result": {"name": "oat cream", "preparation": "stirred",
                    "wording_changes": [
                        {"old": "Whip the cream with the whisk.",
                         "new": "Stir the oat cream."}],
                    "remove_equipment": ["whisk"]}},
    ]


def _self_test():
    base = _sample_recipe()
    catalog = _sample_catalog()
    request = {"target_yield": Fraction(5), "available_equipment": ["pot"],
               "excluded": ["butter", "cream", "coconut cream"]}
    snapshot = copy.deepcopy((base, request, catalog))

    out = adapt(base, request, catalog)
    assert out["possible"] and out["reasons"] == []
    by_name = {i["name"]: i for i in out["recipe"]["ingredients"]}

    # scaling 3 -> 5 servings
    assert out["recipe"]["yield"] == Fraction(5)
    assert by_name["pasta"]["quantity"] == Fraction(500)

    # chained substitution: cream -> coconut cream -> oat cream, with the
    # reintroduced exclusion (coconut cream) detected and resolved
    assert by_name["oat cream"]["quantity"] == Fraction(5, 6)
    assert by_name["oat cream"]["preparation"] == "stirred"

    # explicit catalog priority: olive oil (1) beats margarine (2)
    assert "margarine" not in by_name
    assert by_name["olive oil"]["quantity"] == Fraction(15, 8)
    assert out["choices"] == ["sub-olive-oil", "sub-coconut-cream",
                              "sub-oat-cream"]

    # substitution changed a referenced verb and removed equipment
    assert out["recipe"]["instructions"][1] == "Stir the oat cream."
    assert out["recipe"]["instructions"][2] == (
        "Warm the olive oil and toss everything together.")
    assert out["recipe"]["equipment"] == ["pot"]
    assert set(out["recipe"]["equipment"]) <= set(
        request["available_equipment"])

    # components scaled; no excluded ingredient remains anywhere
    garnish = out["recipe"]["components"][0]
    assert garnish["ingredients"][0]["quantity"] == Fraction(5, 3)
    for ingredient in (out["recipe"]["ingredients"]
                       + garnish["ingredients"]):
        assert ingredient["name"] not in set(request["excluded"])
    assert out["warnings"] == sorted(out["warnings"])

    # exact original print after adaptation; no input mutation
    assert print_original(base) == base["authored_text"]
    assert (base, request, catalog) == snapshot

    # exclusion reintroduced later with no onward substitution
    out2 = adapt(base, {"target_yield": Fraction(3),
                        "available_equipment": ["pot", "whisk"],
                        "excluded": ["cream", "coconut cream"]},
                 [c for c in catalog if c["id"] != "sub-oat-cream"])
    assert not out2["possible"] and out2["recipe"] is None
    assert out2["reasons"] == ["no substitution for coconut cream"]

    # multiple simultaneous impossibility reasons, sorted
    out3 = adapt(base, {"target_yield": Fraction(4),
                        "available_equipment": ["pot"],
                        "excluded": ["parsley"]}, [])
    assert not out3["possible"] and out3["recipe"] is None
    assert out3["reasons"] == ["equipment whisk unavailable",
                               "no substitution for parsley"]

    # substitution cycle
    cyclic = [
        {"id": "b-to-g", "for": "butter", "priority": 1,
         "result": {"name": "ghee"}},
        {"id": "g-to-b", "for": "ghee", "priority": 1,
         "result": {"name": "butter"}},
    ]
    out4 = adapt(base, {"target_yield": Fraction(3),
                        "available_equipment": ["pot", "whisk"],
                        "excluded": ["butter", "ghee"]}, cyclic)
    assert not out4["possible"]
    assert out4["reasons"] == ["substitution cycle involving butter"]

    # catalog order breaks priority ties deterministically
    tied = [
        {"id": "first", "for": "cream", "priority": 1,
         "result": {"name": "oat cream"}},
        {"id": "second", "for": "cream", "priority": 1,
         "result": {"name": "soy cream"}},
    ]
    out5 = adapt(base, {"target_yield": Fraction(3),
                        "available_equipment": ["pot", "whisk"],
                        "excluded": ["cream"]}, tied)
    assert out5["possible"] and out5["choices"] == ["first"]

    # additional ingredients feed back through exclusions and the catalog
    adding = [
        {"id": "oil-plus-salt", "for": "butter", "priority": 1,
         "result": {"name": "olive oil",
                    "additional_ingredients": [
                        {"name": "salt", "quantity": Fraction(1, 4),
                         "unit": "tsp"}]}},
        {"id": "sea-salt", "for": "salt", "priority": 1,
         "result": {"name": "sea salt"}},
    ]
    out6 = adapt(base, {"target_yield": Fraction(5),
                        "available_equipment": ["pot", "whisk"],
                        "excluded": ["butter", "salt"]}, adding)
    assert out6["possible"]
    names6 = {i["name"]: i for i in out6["recipe"]["ingredients"]}
    assert names6["sea salt"]["quantity"] == Fraction(5, 12)
    assert out6["choices"] == ["oil-plus-salt", "sea-salt"]

    print("all self-tests passed")


if __name__ == "__main__":
    _self_test()
