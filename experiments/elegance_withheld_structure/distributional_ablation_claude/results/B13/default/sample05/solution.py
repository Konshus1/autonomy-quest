"""recipebook: adapt recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog, using only the stdlib."""

from fractions import Fraction

_ADD_EQUIPMENT_KEYS = ("equipment_add", "equipment_additions", "add_equipment")
_REMOVE_EQUIPMENT_KEYS = ("equipment_remove", "equipment_removals", "remove_equipment")
_EXTRA_INGREDIENT_KEYS = ("additional_ingredients", "extra_ingredients", "add_ingredients")


def print_original(recipe):
    """Return the authored text exactly as written."""
    return recipe["authored_text"]


def _listed(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if value:
            return value
    return []


def _index_catalog(catalog):
    by_name = {}
    for position, entry in enumerate(catalog):
        by_name.setdefault(entry["for"], []).append((entry["priority"], position, entry))
    for options in by_name.values():
        options.sort(key=lambda option: (option[0], option[1]))
    return by_name


class _Context:
    """Accumulates the observable effects of substitution decisions."""

    def __init__(self):
        self.reasons = set()
        self.warnings = set()
        self.choices = []
        self.wording = []
        self.equipment_added = []
        self.equipment_removed = []
        self.yield_factor = Fraction(1)
        self.failed = False


def _resolve(ingredient, chain, excluded, by_name, ctx):
    """Resolve one ingredient to a list of allowed ingredients, or None.

    Replacements and additional ingredients are re-resolved against the same
    exclusions, so chained substitutions and reintroduced exclusions are
    handled; `chain` holds names already substituted on this path so a
    revisit is a cycle.
    """
    name = ingredient["name"]
    if name not in excluded:
        return [dict(ingredient)]
    if name in chain:
        ctx.reasons.add("substitution cycle involving %s" % name)
        ctx.failed = True
        return None
    options = by_name.get(name)
    if not options:
        ctx.reasons.add("no substitution for %s" % name)
        ctx.failed = True
        return None
    choice = options[0][2]
    result = choice["result"]
    ctx.choices.append(choice["id"])
    replaced = dict(ingredient)
    replaced["name"] = result["name"]
    if "quantity_factor" in result:
        replaced["quantity"] = Fraction(ingredient["quantity"]) * Fraction(result["quantity_factor"])
    if "unit" in result:
        replaced["unit"] = result["unit"]
    if "preparation" in result:
        replaced["preparation"] = result["preparation"]
    for pair in result.get("wording_changes") or []:
        ctx.wording.append((pair["old"], pair["new"]))
    ctx.equipment_added.extend(_listed(result, _ADD_EQUIPMENT_KEYS))
    ctx.equipment_removed.extend(_listed(result, _REMOVE_EQUIPMENT_KEYS))
    if "yield_factor" in result:
        ctx.yield_factor *= Fraction(result["yield_factor"])
        ctx.warnings.add("substitution %s changes yield" % choice["id"])
    if replaced["name"] in excluded:
        ctx.warnings.add(
            "substitution %s introduces excluded ingredient %s"
            % (choice["id"], replaced["name"])
        )
    deeper_chain = chain | {name}
    resolved = []
    ok = True
    branch = _resolve(replaced, deeper_chain, excluded, by_name, ctx)
    if branch is None:
        ok = False
    else:
        resolved.extend(branch)
    for extra in _listed(result, _EXTRA_INGREDIENT_KEYS):
        branch = _resolve(dict(extra), deeper_chain, excluded, by_name, ctx)
        if branch is None:
            ok = False
        else:
            resolved.extend(branch)
    return resolved if ok else None


def _scaled(ingredient, scale):
    scaled = dict(ingredient)
    scaled["quantity"] = Fraction(ingredient["quantity"]) * scale
    return scaled


def _apply_wording(lines, wording):
    rewritten = []
    for line in lines:
        for old, new in wording:
            line = line.replace(old, new)
        rewritten.append(line)
    return rewritten


def adapt(recipe, request, catalog):
    excluded = set(request.get("excluded") or [])
    available = set(request.get("available_equipment") or [])
    target_yield = Fraction(request["target_yield"])
    by_name = _index_catalog(catalog)

    ctx = _Context()
    ingredients = []
    for ingredient in recipe["ingredients"]:
        resolved = _resolve(ingredient, frozenset(), excluded, by_name, ctx)
        if resolved is not None:
            ingredients.extend(resolved)

    # Components are optional: a component that cannot be adapted is omitted
    # with a warning instead of making the whole adaptation impossible.
    kept = []
    for component in recipe.get("components") or []:
        local = _Context()
        component_ingredients = []
        for ingredient in component.get("ingredients") or []:
            resolved = _resolve(ingredient, frozenset(), excluded, by_name, local)
            if resolved is not None:
                component_ingredients.extend(resolved)
        component_equipment = set(component.get("equipment") or [])
        component_equipment.update(local.equipment_added)
        component_equipment.difference_update(local.equipment_removed)
        if local.failed or not component_equipment <= available:
            label = component.get("name") or component.get("id") or "component"
            ctx.warnings.add("component %s omitted" % label)
            continue
        ctx.warnings.update(local.warnings)
        ctx.choices.extend(local.choices)
        ctx.wording.extend(local.wording)
        ctx.yield_factor *= local.yield_factor
        kept.append((component, component_ingredients, sorted(component_equipment)))

    equipment = set(recipe["equipment"])
    equipment.update(ctx.equipment_added)
    equipment.difference_update(ctx.equipment_removed)
    for item in sorted(equipment - available):
        ctx.reasons.add("equipment %s unavailable" % item)
        ctx.failed = True

    if ctx.failed:
        return {
            "possible": False,
            "recipe": None,
            "choices": ctx.choices,
            "warnings": sorted(ctx.warnings),
            "reasons": sorted(ctx.reasons),
        }

    scale = target_yield / (Fraction(recipe["yield"]) * ctx.yield_factor)
    adapted_components = []
    for component, component_ingredients, component_equipment in kept:
        rebuilt = dict(component)
        rebuilt["ingredients"] = [_scaled(i, scale) for i in component_ingredients]
        if "equipment" in component:
            rebuilt["equipment"] = component_equipment
        if "instructions" in component:
            rebuilt["instructions"] = _apply_wording(component["instructions"], ctx.wording)
        adapted_components.append(rebuilt)

    adapted = {
        "title": recipe["title"],
        "yield": target_yield,
        "ingredients": [_scaled(i, scale) for i in ingredients],
        "equipment": sorted(equipment),
        "instructions": _apply_wording(recipe["instructions"], ctx.wording),
        "components": adapted_components,
        "authored_text": recipe["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": ctx.choices,
        "warnings": sorted(ctx.warnings),
        "reasons": [],
    }


if __name__ == "__main__":
    import copy

    F = Fraction
    recipe = {
        "title": "Pancakes",
        "yield": F(3),
        "ingredients": [
            {"id": "flour", "name": "flour", "quantity": F(300), "unit": "g", "preparation": "sifted"},
            {"id": "butter", "name": "butter", "quantity": F(60), "unit": "g", "preparation": "melted"},
            {"id": "milk", "name": "milk", "quantity": F(1, 2), "unit": "l", "preparation": ""},
        ],
        "equipment": ["skillet", "whisk"],
        "instructions": [
            "Melt the butter in the skillet.",
            "Whisk the milk into the flour.",
        ],
        "components": [],
        "authored_text": (
            "Pancakes (serves 3)\n300 g flour, sifted\n60 g butter, melted\n"
            "1/2 l milk\nMelt the butter in the skillet.\nWhisk the milk into the flour.\n"
        ),
    }
    catalog = [
        {"id": "butter->margarine", "for": "butter", "priority": 2,
         "result": {"name": "margarine"}},
        {"id": "butter->oil", "for": "butter", "priority": 1,
         "result": {"name": "oil", "quantity_factor": F(3, 4), "unit": "ml",
                    "preparation": "",
                    "wording_changes": [{"old": "Melt the butter in the skillet",
                                         "new": "Pour the oil into the saucepan"}],
                    "equipment_add": ["saucepan"],
                    "equipment_remove": ["skillet"]}},
        {"id": "milk->cream", "for": "milk", "priority": 1, "result": {"name": "cream"}},
        {"id": "milk->soy milk", "for": "milk", "priority": 1, "result": {"name": "soy milk"}},
        {"id": "cream->oat milk", "for": "cream", "priority": 1, "result": {"name": "oat milk"}},
        {"id": "cheese->vegan cheese", "for": "cheese", "priority": 1, "result": {"name": "vegan cheese"}},
        {"id": "vegan cheese->cheese", "for": "vegan cheese", "priority": 1, "result": {"name": "cheese"}},
    ]
    pristine = copy.deepcopy(recipe)

    # 1. Scaling 3 -> 5 servings.
    out = adapt(recipe, {"target_yield": F(5), "available_equipment": ["skillet", "whisk"], "excluded": []}, catalog)
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == F(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == F(500)
    assert out["recipe"]["ingredients"][2]["quantity"] == F(5, 6)

    # 2. Chained substitution with a reintroduced exclusion; milk->cream and
    #    milk->soy milk share priority 1, so catalog order picks milk->cream,
    #    whose replacement is itself excluded and is substituted again.
    out = adapt(recipe, {"target_yield": F(3), "available_equipment": ["skillet", "whisk"],
                         "excluded": ["milk", "cream"]}, catalog)
    assert out["possible"]
    assert out["choices"] == ["milk->cream", "cream->oat milk"]
    assert out["recipe"]["ingredients"][2]["name"] == "oat milk"
    assert any("introduces excluded ingredient cream" in w for w in out["warnings"])

    # 3. Substitution changing a referenced verb and equipment; explicit
    #    priority 1 beats the margarine entry listed first at priority 2.
    out = adapt(recipe, {"target_yield": F(5), "available_equipment": ["saucepan", "whisk"],
                         "excluded": ["butter"]}, catalog)
    assert out["possible"] and out["choices"] == ["butter->oil"]
    oil = out["recipe"]["ingredients"][1]
    assert oil["name"] == "oil" and oil["quantity"] == F(75) and oil["unit"] == "ml"
    assert out["recipe"]["equipment"] == ["saucepan", "whisk"]
    assert out["recipe"]["instructions"][0] == "Pour the oil into the saucepan."

    # 4. Multiple simultaneous impossibility reasons, all reported and sorted.
    out = adapt(recipe, {"target_yield": F(5), "available_equipment": ["skillet"],
                         "excluded": ["flour"]}, catalog)
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["equipment whisk unavailable", "no substitution for flour"]

    # 5. Substitution cycle.
    cheesy = dict(pristine, title="Toastie",
                  ingredients=[{"id": "cheese", "name": "cheese", "quantity": F(100),
                                "unit": "g", "preparation": "grated"}],
                  equipment=["skillet"], instructions=["Melt the cheese."])
    out = adapt(cheesy, {"target_yield": F(3), "available_equipment": ["skillet"],
                         "excluded": ["cheese", "vegan cheese"]}, catalog)
    assert not out["possible"]
    assert out["reasons"] == ["substitution cycle involving cheese"]

    # 6. Original prints byte-for-byte and inputs were never mutated.
    assert print_original(recipe) == pristine["authored_text"]
    assert recipe == pristine

    print("all acceptance checks passed")
