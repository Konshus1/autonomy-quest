"""recipebook: adapt authored recipes to yield, equipment, and dietary constraints.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Neither function mutates its inputs.
"""

from fractions import Fraction
import copy


def print_original(recipe):
    return recipe["authored_text"]


def _catalog_index(catalog):
    by_name = {}
    for position, choice in enumerate(catalog):
        by_name.setdefault(choice["for"], []).append(
            (choice["priority"], position, choice)
        )
    for options in by_name.values():
        options.sort(key=lambda entry: (entry[0], entry[1]))
    return by_name


def adapt(recipe, request, catalog):
    target = request["target_yield"]
    available = set(request["available_equipment"])
    excluded = set(request["excluded"])
    scale = target / recipe["yield"]
    by_name = _catalog_index(catalog)

    choices = []
    warnings = set()
    reasons = set()
    equipment = set(recipe["equipment"])
    wording = []
    state = {"yield_factor": Fraction(1)}

    def resolve(ingredient, chain):
        ingredient = dict(ingredient)
        ingredient["quantity"] = ingredient["quantity"] * scale
        extras = []
        while ingredient["name"] in excluded:
            name = ingredient["name"]
            if chain:
                warnings.add(f"substitution reintroduced excluded ingredient {name}")
            if name in chain:
                reasons.add(f"substitution cycle involving {name}")
                return None, extras
            chain = chain | {name}
            options = by_name.get(name)
            if not options:
                reasons.add(f"no substitution for {name}")
                return None, extras
            chosen = options[0][2]
            choices.append(chosen["id"])
            result = chosen["result"]
            ingredient["name"] = result["name"]
            if "quantity_factor" in result:
                ingredient["quantity"] = ingredient["quantity"] * result["quantity_factor"]
            if "unit" in result:
                ingredient["unit"] = result["unit"]
            if "preparation" in result:
                ingredient["preparation"] = result["preparation"]
            for change in result.get("wording_changes", []):
                wording.append((change["old"], change["new"]))
            for item in result.get("equipment_add", []):
                equipment.add(item)
            for item in result.get("equipment_remove", []):
                equipment.discard(item)
            if "yield_factor" in result:
                state["yield_factor"] *= result["yield_factor"]
            for extra in result.get("additional_ingredients", []):
                extras.append((extra, chain))
        return ingredient, extras

    def resolve_list(source_ingredients):
        resolved = []
        queue = [(item, frozenset()) for item in source_ingredients]
        while queue:
            item, chain = queue.pop(0)
            done, extras = resolve(item, chain)
            if done is not None:
                resolved.append(done)
            queue.extend(extras)
        return resolved

    ingredients = resolve_list(recipe["ingredients"])

    components = []
    for component in recipe.get("components", []):
        adapted_component = copy.deepcopy(component)
        if "ingredients" in component:
            adapted_component["ingredients"] = resolve_list(component["ingredients"])
        components.append(adapted_component)

    instructions = list(recipe["instructions"])
    for old, new in wording:
        instructions = [line.replace(old, new) for line in instructions]

    if state["yield_factor"] != 1:
        # A substitution altered the yield; renormalize so the recipe stays
        # at the requested target yield.
        correction = Fraction(1) / state["yield_factor"]
        for item in ingredients:
            item["quantity"] = item["quantity"] * correction
        for component in components:
            for item in component.get("ingredients", []):
                item["quantity"] = item["quantity"] * correction
        warnings.add(f"yield rescaled to target {target} after substitutions")

    for item in equipment:
        if item not in available:
            reasons.add(f"equipment {item} unavailable")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

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


if __name__ == "__main__":
    def base_recipe():
        return {
            "title": "Pancakes",
            "yield": Fraction(3),
            "ingredients": [
                {"id": "flour", "name": "flour", "quantity": Fraction(3, 2),
                 "unit": "cup", "preparation": "sifted"},
                {"id": "butter", "name": "butter", "quantity": Fraction(3),
                 "unit": "tbsp", "preparation": "melted"},
                {"id": "milk", "name": "milk", "quantity": Fraction(1),
                 "unit": "cup", "preparation": "room temperature"},
            ],
            "equipment": ["skillet", "whisk"],
            "instructions": [
                "Melt the butter in the skillet.",
                "Whisk the milk into the flour.",
            ],
            "components": [],
            "authored_text": ("Pancakes\nServes 3\nMelt the butter in the skillet.\n"
                              "Whisk the milk into the flour.\n"),
        }

    plain_request = {"target_yield": Fraction(5),
                     "available_equipment": ["skillet", "whisk", "saucepan"],
                     "excluded": []}

    # 1. Scaling 3 -> 5 servings.
    out = adapt(base_recipe(), plain_request, [])
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == Fraction(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == Fraction(5, 2)
    assert out["recipe"]["ingredients"][1]["quantity"] == Fraction(5)

    # 2. Chained substitution: butter -> margarine -> oil.
    catalog2 = [
        {"id": "sub-butter-margarine", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "sub-margarine-oil", "for": "margarine", "priority": 1,
         "result": {"name": "oil", "quantity_factor": Fraction(3, 4),
                    "unit": "tbsp", "preparation": ""}},
    ]
    req2 = {"target_yield": Fraction(3),
            "available_equipment": ["skillet", "whisk"],
            "excluded": ["butter", "margarine"]}
    out = adapt(base_recipe(), req2, catalog2)
    assert out["possible"]
    assert out["choices"] == ["sub-butter-margarine", "sub-margarine-oil"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "oil" in names and "butter" not in names and "margarine" not in names
    assert out["recipe"]["ingredients"][1]["quantity"] == Fraction(9, 4)
    assert any("margarine" in w for w in out["warnings"])

    # 3. Substitution changing a referenced verb and equipment.
    catalog3 = [
        {"id": "sub-butter-oil", "for": "butter", "priority": 1,
         "result": {"name": "oil",
                    "wording_changes": [{"old": "Melt the butter in the skillet.",
                                         "new": "Heat the oil in the saucepan."}],
                    "equipment_add": ["saucepan"],
                    "equipment_remove": ["skillet"]}},
    ]
    req3 = {"target_yield": Fraction(3),
            "available_equipment": ["saucepan", "whisk"],
            "excluded": ["butter"]}
    out = adapt(base_recipe(), req3, catalog3)
    assert out["possible"]
    assert out["recipe"]["instructions"][0] == "Heat the oil in the saucepan."
    assert out["recipe"]["equipment"] == ["saucepan", "whisk"]

    # 4. Exclusion reintroduced later by an additional ingredient.
    catalog4 = [
        {"id": "sub-milk-oat", "for": "milk", "priority": 1,
         "result": {"name": "oat milk",
                    "additional_ingredients": [
                        {"id": "honey", "name": "honey",
                         "quantity": Fraction(1, 2), "unit": "tbsp",
                         "preparation": ""}]}},
        {"id": "sub-honey-syrup", "for": "honey", "priority": 1,
         "result": {"name": "maple syrup"}},
    ]
    req4 = {"target_yield": Fraction(3),
            "available_equipment": ["skillet", "whisk"],
            "excluded": ["milk", "honey"]}
    out = adapt(base_recipe(), req4, catalog4)
    assert out["possible"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "maple syrup" in names and "honey" not in names and "milk" not in names
    assert out["choices"] == ["sub-milk-oat", "sub-honey-syrup"]
    assert any("honey" in w for w in out["warnings"])

    # 5. Multiple simultaneous impossibility reasons, including a cycle.
    catalog5 = [
        {"id": "sub-milk-cream", "for": "milk", "priority": 1,
         "result": {"name": "cream"}},
        {"id": "sub-cream-milk", "for": "cream", "priority": 1,
         "result": {"name": "milk"}},
    ]
    req5 = {"target_yield": Fraction(4),
            "available_equipment": ["whisk"],
            "excluded": ["butter", "milk", "cream"]}
    out = adapt(base_recipe(), req5, catalog5)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["equipment skillet unavailable",
                              "no substitution for butter",
                              "substitution cycle involving milk"]

    # 6. Exact original print after adaptations; inputs never mutated.
    original = base_recipe()
    snapshot = copy.deepcopy(original)
    adapt(original, req5, catalog5)
    adapt(original, plain_request, [])
    assert original == snapshot
    assert print_original(original) == snapshot["authored_text"]

    # 7. Deterministic choice by explicit priority, catalog order tie-break.
    catalog7 = [
        {"id": "sub-late-low", "for": "butter", "priority": 2,
         "result": {"name": "shortening"}},
        {"id": "sub-early-best", "for": "butter", "priority": 1,
         "result": {"name": "oil"}},
        {"id": "sub-tie-second", "for": "butter", "priority": 1,
         "result": {"name": "ghee"}},
    ]
    req7 = {"target_yield": Fraction(3),
            "available_equipment": ["skillet", "whisk"],
            "excluded": ["butter"]}
    out = adapt(base_recipe(), req7, catalog7)
    assert out["choices"] == ["sub-early-best"]
    assert "oil" in [i["name"] for i in out["recipe"]["ingredients"]]

    print("all acceptance checks passed")
