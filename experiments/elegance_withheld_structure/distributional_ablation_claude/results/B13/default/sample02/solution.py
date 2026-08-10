"""recipebook.py — adapt recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Neither function mutates its inputs.
"""

from fractions import Fraction
import copy


def print_original(recipe):
    """Return the recipe exactly as authored, byte for byte."""
    return recipe["authored_text"]


def _candidates(catalog, name):
    """Catalog choices for `name`, best first: lower priority wins,
    catalog order breaks ties."""
    ranked = [
        (choice["priority"], index, choice)
        for index, choice in enumerate(catalog)
        if choice["for"] == name
    ]
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [choice for _, _, choice in ranked]


def adapt(recipe, request, catalog):
    scale = Fraction(request["target_yield"]) / Fraction(recipe["yield"])
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])

    equipment = set(recipe["equipment"])
    reasons = set()
    warnings = set()
    choices = []
    wording_changes = []

    def resolve(ingredient):
        """Scale one ingredient and substitute until it is no longer excluded.
        Returns a list of resulting ingredients (replacement plus any
        additional ingredients the chosen substitutions introduced), or None
        if no acceptable resolution exists; reasons are recorded either way."""
        current = {
            "id": ingredient.get("id", ingredient["name"]),
            "name": ingredient["name"],
            "quantity": Fraction(ingredient["quantity"]) * scale,
            "unit": ingredient.get("unit", ""),
            "preparation": ingredient.get("preparation", ""),
        }
        produced = []
        seen = {current["name"]}
        ok = True
        while current["name"] in excluded:
            options = _candidates(catalog, current["name"])
            if not options:
                reasons.add("no substitution for %s" % current["name"])
                ok = False
                break
            chosen = options[0]
            choices.append(chosen["id"])
            result = chosen["result"]
            replacement = result["name"]
            if replacement in seen:
                reasons.add("substitution cycle involving %s" % replacement)
                ok = False
                break
            seen.add(replacement)
            current["name"] = replacement
            if "quantity_factor" in result:
                current["quantity"] = current["quantity"] * Fraction(
                    result["quantity_factor"]
                )
            if "unit" in result and result["unit"] != current["unit"]:
                warnings.add("unit changed for %s" % replacement)
                current["unit"] = result["unit"]
            if "preparation" in result:
                current["preparation"] = result["preparation"]
            for change in result.get("wording_changes", []):
                wording_changes.append((change["old"], change["new"]))
            for item in result.get("add_equipment", []):
                equipment.add(item)
            for item in result.get("remove_equipment", []):
                equipment.discard(item)
            if "yield_factor" in result:
                warnings.add("yield affected by substituting %s" % replacement)
            for extra in result.get("additional_ingredients", []):
                resolved = resolve(extra)
                if resolved is None:
                    ok = False
                else:
                    produced.extend(resolved)
        if not ok:
            return None
        return [current] + produced

    feasible = True

    new_ingredients = []
    for ingredient in recipe["ingredients"]:
        resolved = resolve(ingredient)
        if resolved is None:
            feasible = False
        else:
            new_ingredients.extend(resolved)

    new_components = []
    for component in recipe.get("components", []):
        rebuilt = {
            key: copy.deepcopy(value)
            for key, value in component.items()
            if key != "ingredients"
        }
        if "ingredients" in component:
            component_ingredients = []
            for ingredient in component["ingredients"]:
                resolved = resolve(ingredient)
                if resolved is None:
                    feasible = False
                else:
                    component_ingredients.extend(resolved)
            rebuilt["ingredients"] = component_ingredients
        new_components.append(rebuilt)

    for item in equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)
            feasible = False

    instructions = []
    for line in recipe["instructions"]:
        text = line
        for old, new in wording_changes:
            text = text.replace(old, new)
        instructions.append(text)

    if not feasible:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    adapted = {
        "title": recipe["title"],
        "yield": Fraction(request["target_yield"]),
        "ingredients": new_ingredients,
        "equipment": sorted(equipment),
        "instructions": instructions,
        "components": new_components,
        "authored_text": recipe["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }


if __name__ == "__main__":
    F = Fraction

    recipe = {
        "title": "Pancakes",
        "yield": F(3),
        "ingredients": [
            {"id": "flour", "name": "flour", "quantity": F(3, 2),
             "unit": "cup", "preparation": "sifted"},
            {"id": "butter", "name": "butter", "quantity": F(3),
             "unit": "tbsp", "preparation": "melted"},
            {"id": "milk", "name": "milk", "quantity": F(1),
             "unit": "cup", "preparation": ""},
        ],
        "equipment": ["skillet", "whisk"],
        "instructions": [
            "Melt the butter in the skillet.",
            "Whisk milk into flour.",
        ],
        "components": [],
        "authored_text": "Pancakes\nServes 3.\n",
    }
    snapshot = copy.deepcopy(recipe)

    catalog = [
        {"id": "butter->margarine", "for": "butter", "priority": 2,
         "result": {"name": "margarine"}},
        {"id": "butter->oil", "for": "butter", "priority": 1,
         "result": {
             "name": "oil", "quantity_factor": F(3, 4), "unit": "tbsp",
             "preparation": "",
             "wording_changes": [
                 {"old": "Melt the butter in the skillet.",
                  "new": "Pour the oil into the saucepan."}],
             "add_equipment": ["saucepan"],
             "remove_equipment": ["skillet"]}},
        {"id": "oil->coconut oil", "for": "oil", "priority": 1,
         "result": {"name": "coconut oil"}},
        {"id": "milk->water+butter", "for": "milk", "priority": 1,
         "result": {
             "name": "water",
             "additional_ingredients": [
                 {"id": "butter-extra", "name": "butter", "quantity": F(1),
                  "unit": "tbsp", "preparation": "melted"}]}},
    ]

    # 1. Scaling 3 -> 5 servings.
    out = adapt(recipe, {"target_yield": F(5),
                         "available_equipment": ["skillet", "whisk"],
                         "excluded": []}, catalog)
    assert out["possible"]
    assert out["recipe"]["yield"] == F(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == F(5, 2)
    assert out["recipe"]["ingredients"][1]["quantity"] == F(5)
    assert out["choices"] == [] and out["reasons"] == []

    # 2. Chained substitution: butter -> oil -> coconut oil.
    out = adapt(recipe, {"target_yield": F(5),
                         "available_equipment": ["saucepan", "whisk"],
                         "excluded": ["butter", "oil"]}, catalog)
    assert out["possible"]
    assert out["choices"] == ["butter->oil", "oil->coconut oil"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "coconut oil" in names and "butter" not in names and "oil" not in names
    assert out["recipe"]["ingredients"][1]["quantity"] == F(15, 4)

    # 3. Substitution changing a referenced verb and equipment, chosen
    #    deterministically by priority (butter->oil beats butter->margarine).
    out = adapt(recipe, {"target_yield": F(3),
                         "available_equipment": ["saucepan", "whisk"],
                         "excluded": ["butter"]}, catalog)
    assert out["possible"]
    assert out["choices"] == ["butter->oil"]
    assert out["recipe"]["instructions"][0] == "Pour the oil into the saucepan."
    assert out["recipe"]["equipment"] == ["saucepan", "whisk"]

    # 4. Exclusion reintroduced later: substituting milk adds butter, which is
    #    itself excluded and gets chained through the catalog again.
    out = adapt(recipe, {"target_yield": F(3),
                         "available_equipment": ["saucepan", "whisk"],
                         "excluded": ["milk", "butter"]}, catalog)
    assert out["possible"]
    assert out["choices"] == ["butter->oil", "milk->water+butter", "butter->oil"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "butter" not in names and "milk" not in names
    assert names.count("oil") == 2 and "water" in names

    # 5. Multiple simultaneous impossibility reasons, all reported and sorted.
    out = adapt(recipe, {"target_yield": F(5),
                         "available_equipment": ["skillet"],
                         "excluded": ["flour", "milk"]}, [])
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["equipment whisk unavailable",
                              "no substitution for flour",
                              "no substitution for milk"]

    # 5b. Substitution cycle.
    cyclic = [
        {"id": "butter->margarine", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "margarine->butter", "for": "margarine", "priority": 1,
         "result": {"name": "butter"}},
    ]
    out = adapt(recipe, {"target_yield": F(3),
                         "available_equipment": ["skillet", "whisk"],
                         "excluded": ["butter", "margarine"]}, cyclic)
    assert not out["possible"]
    assert out["reasons"] == ["substitution cycle involving butter"]
    assert out["choices"] == ["butter->margarine", "margarine->butter"]

    # 6. Exact original print after adaptations; inputs never mutated.
    assert print_original(recipe) == "Pancakes\nServes 3.\n"
    assert recipe == snapshot

    print("all acceptance checks passed")
