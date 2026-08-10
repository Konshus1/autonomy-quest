"""recipebook: adapt authored recipes to a target yield, available equipment,
dietary exclusions, and a prioritized substitution catalog.

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Only the standard library is used, and neither public function mutates its
arguments.  The original recipe's authored_text is always returned verbatim.
"""

from fractions import Fraction


def print_original(recipe):
    """Return the authored recipe text exactly as written."""
    return recipe["authored_text"]


def _norm(name):
    return name.strip().lower()


def _ranked(catalog):
    """Catalog entries ordered by (priority, authored position)."""
    return sorted(enumerate(catalog), key=lambda pair: (pair[1]["priority"], pair[0]))


def _pick(name, ranked):
    key = _norm(name)
    for _, choice in ranked:
        if _norm(choice["for"]) == key:
            return choice
    return None


def _replace(ingredient, result):
    new = dict(ingredient)
    new["name"] = result["name"]
    if "quantity_factor" in result:
        new["quantity"] = new["quantity"] * Fraction(result["quantity_factor"])
    if "unit" in result:
        new["unit"] = result["unit"]
    if "preparation" in result:
        new["preparation"] = result["preparation"]
    return new


def adapt(recipe, request, catalog):
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])
    excluded = {_norm(n) for n in request.get("excluded", [])}
    available = set(request.get("available_equipment", []))
    ranked = _ranked(catalog)

    choices = []
    warnings = []
    reasons = []
    instructions = list(recipe.get("instructions", []))
    equipment = set(recipe.get("equipment", []))
    state = {"yield_mult": Fraction(1)}

    def note(sink, text):
        if text not in sink:
            sink.append(text)

    def resolve(ingredient, reason_sink):
        """Scale one ingredient and chain substitutions until nothing about it
        is excluded.  Returns the resulting ingredient list (the replacement
        plus any additional ingredients it introduced), or None when the chain
        dead-ends or cycles; the reason is recorded in reason_sink either way
        and resolution of other ingredients continues."""
        current = dict(ingredient)
        current["quantity"] = Fraction(current["quantity"]) * scale
        extras = []
        seen = set()
        failed = False
        while _norm(current["name"]) in excluded:
            key = _norm(current["name"])
            if key in seen:
                note(reason_sink, "substitution cycle involving " + current["name"])
                return None
            seen.add(key)
            choice = _pick(current["name"], ranked)
            if choice is None:
                note(reason_sink, "no substitution for " + current["name"])
                return None
            choices.append(choice["id"])
            result = choice["result"]
            for change in result.get("wording_changes", []):
                for i, line in enumerate(instructions):
                    instructions[i] = line.replace(change["old"], change["new"])
            for item in result.get("equipment_remove", []):
                equipment.discard(item)
            for item in result.get("equipment_add", []):
                equipment.add(item)
            if "yield_factor" in result:
                state["yield_mult"] *= Fraction(result["yield_factor"])
            for extra in result.get("additional_ingredients", []):
                resolved = resolve(extra, reason_sink)
                if resolved is None:
                    failed = True
                else:
                    extras.extend(resolved)
            replacement = _replace(current, result)
            if _norm(replacement["name"]) in excluded:
                note(warnings, "substitution %s reintroduces excluded ingredient %s"
                     % (choice["id"], replacement["name"]))
            current = replacement
        if failed:
            return None
        return [current] + extras

    ingredients_out = []
    for ingredient in recipe.get("ingredients", []):
        resolved = resolve(ingredient, reasons)
        if resolved is not None:
            ingredients_out.extend(resolved)

    components_out = []
    for component in recipe.get("components", []):
        snapshot = (len(choices), list(warnings), list(instructions),
                    set(equipment), state["yield_mult"])
        local_reasons = []
        component_ingredients = []
        for ingredient in component.get("ingredients", []):
            resolved = resolve(ingredient, local_reasons)
            if resolved is not None:
                component_ingredients.extend(resolved)
        if local_reasons and component.get("optional"):
            del choices[snapshot[0]:]
            warnings[:] = snapshot[1]
            instructions[:] = snapshot[2]
            equipment.clear()
            equipment.update(snapshot[3])
            state["yield_mult"] = snapshot[4]
            note(warnings, "omitted optional component " + component["name"])
            continue
        for text in local_reasons:
            note(reasons, text)
        adapted_component = dict(component)
        adapted_component["ingredients"] = component_ingredients
        components_out.append(adapted_component)

    for item in equipment:
        if item not in available:
            note(reasons, "equipment %s unavailable" % item)

    if state["yield_mult"] != 1:
        rebalance = Fraction(1) / state["yield_mult"]
        for ingredient in ingredients_out:
            ingredient["quantity"] *= rebalance
        for component in components_out:
            for ingredient in component["ingredients"]:
                ingredient["quantity"] *= rebalance
        note(warnings, "quantities rebalanced to hold the target yield "
                       "after a yield-changing substitution")

    if reasons:
        return {"possible": False, "recipe": None, "choices": choices,
                "warnings": sorted(warnings), "reasons": sorted(reasons)}

    adapted = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": ingredients_out,
        "equipment": sorted(equipment),
        "instructions": instructions,
        "components": components_out,
        "authored_text": recipe["authored_text"],
    }
    return {"possible": True, "recipe": adapted, "choices": choices,
            "warnings": sorted(warnings), "reasons": []}


if __name__ == "__main__":
    import copy

    F = Fraction

    recipe = {
        "title": "Butter Cake",
        "yield": F(3),
        "ingredients": [
            {"id": "flour", "name": "flour", "quantity": F(3, 2),
             "unit": "cup", "preparation": "sifted"},
            {"id": "butter", "name": "butter", "quantity": F(6),
             "unit": "tbsp", "preparation": "creamed until fluffy"},
            {"id": "milk", "name": "milk", "quantity": F(3, 4),
             "unit": "cup", "preparation": "warmed"},
        ],
        "equipment": ["cake pan", "stand mixer"],
        "instructions": [
            "Cream the butter until fluffy.",
            "Fold in the sifted flour.",
            "Warm the milk and combine.",
        ],
        "components": [
            {"name": "glaze", "optional": True, "ingredients": [
                {"id": "honey", "name": "honey", "quantity": F(2),
                 "unit": "tbsp", "preparation": "drizzled"},
            ]},
        ],
        "authored_text": "Butter Cake — serves 3.\nCream the butter until fluffy…\n",
    }
    frozen = copy.deepcopy(recipe)

    # 1. Scaling 3 -> 5 servings with exact rational quantities.
    out = adapt(recipe, {"target_yield": F(5),
                         "available_equipment": ["cake pan", "stand mixer"],
                         "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == F(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == F(5, 2)
    assert out["recipe"]["ingredients"][1]["quantity"] == F(10)
    assert out["recipe"]["ingredients"][2]["quantity"] == F(5, 4)

    # 2. Chained substitution: the first replacement is itself excluded,
    #    which is detected and resolved by a second catalog choice.
    chain_catalog = [
        {"id": "milk->oat", "for": "milk", "priority": 0,
         "result": {"name": "oat milk"}},
        {"id": "oat->soy", "for": "oat milk", "priority": 0,
         "result": {"name": "soy milk"}},
    ]
    out = adapt(recipe, {"target_yield": F(3),
                         "available_equipment": ["cake pan", "stand mixer"],
                         "excluded": ["milk", "oat milk"]}, chain_catalog)
    assert out["possible"]
    assert out["choices"] == ["milk->oat", "oat->soy"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "soy milk" in names and "milk" not in names and "oat milk" not in names
    assert any("reintroduces" in w for w in out["warnings"])

    # 3. Substitution changing a referenced verb and equipment, selected by
    #    explicit priority (0 beats 1 despite catalog order).
    verb_catalog = [
        {"id": "butter->margarine", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "butter->oil", "for": "butter", "priority": 0,
         "result": {"name": "oil", "quantity_factor": F(3, 4), "unit": "cup",
                    "preparation": "whisked until glossy",
                    "wording_changes": [{"old": "Cream the butter",
                                         "new": "Whisk the oil"}],
                    "equipment_remove": ["stand mixer"],
                    "equipment_add": ["whisk"]}},
    ]
    out = adapt(recipe, {"target_yield": F(3),
                         "available_equipment": ["cake pan", "whisk"],
                         "excluded": ["butter"]}, verb_catalog)
    assert out["possible"]
    assert out["choices"] == ["butter->oil"]
    assert out["recipe"]["instructions"][0] == "Whisk the oil until fluffy."
    assert out["recipe"]["equipment"] == ["cake pan", "whisk"]
    oil = next(i for i in out["recipe"]["ingredients"] if i["name"] == "oil")
    assert oil["quantity"] == F(6) * F(3, 4) and oil["unit"] == "cup"

    # Deterministic tie-break: equal priorities fall back to catalog order.
    tie_catalog = [
        {"id": "butter->ghee", "for": "butter", "priority": 0,
         "result": {"name": "ghee"}},
        {"id": "butter->oil2", "for": "butter", "priority": 0,
         "result": {"name": "oil"}},
    ]
    out = adapt(recipe, {"target_yield": F(3),
                         "available_equipment": ["cake pan", "stand mixer"],
                         "excluded": ["butter"]}, tie_catalog)
    assert out["choices"] == ["butter->ghee"]

    # 4. Multiple simultaneous impossibility reasons: a missing substitution,
    #    a substitution cycle, and two unavailable equipment items, all
    #    reported together and sorted.
    cycle_catalog = [
        {"id": "milk->oat", "for": "milk", "priority": 0,
         "result": {"name": "oat milk"}},
        {"id": "oat->milk", "for": "oat milk", "priority": 0,
         "result": {"name": "milk"}},
    ]
    out = adapt(recipe, {"target_yield": F(3), "available_equipment": [],
                         "excluded": ["butter", "milk", "oat milk"]},
                cycle_catalog)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == sorted([
        "equipment cake pan unavailable",
        "equipment stand mixer unavailable",
        "no substitution for butter",
        "substitution cycle involving milk",
    ])

    # 5. Exact original print and no input mutation after all adaptations.
    assert print_original(recipe) == frozen["authored_text"]
    assert recipe == frozen

    print("all acceptance checks passed")
