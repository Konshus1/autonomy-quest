"""recipebook.py -- adapt recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.
Only the Python standard library is used, and neither function mutates its
inputs.
"""

from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as authored, byte-for-byte."""
    return recipe["authored_text"]


def _rank(catalog):
    """Catalog choices ordered by (priority, catalog position)."""
    order = sorted(range(len(catalog)), key=lambda i: (catalog[i]["priority"], i))
    return [catalog[i] for i in order]


def _first(mapping, keys, default):
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


class _Session:
    """Accumulated effects of the substitution choices applied so far."""

    def __init__(self):
        self.choices = []        # catalog IDs in application order
        self.warnings = []
        self.reasons = set()     # every independently applicable reason
        self.wording = []        # (old, new) pairs in application order
        self.equip_add = []
        self.equip_remove = []

    def merge(self, other):
        self.choices.extend(other.choices)
        self.warnings.extend(other.warnings)
        self.reasons |= other.reasons
        self.wording.extend(other.wording)
        self.equip_add.extend(other.equip_add)
        self.equip_remove.extend(other.equip_remove)


def _resolve(ingredient, excluded, ranked, scale, sess, seen):
    """Resolve one already-scaled ingredient against the exclusions.

    Returns the list of ingredients it becomes (the replacement plus any
    additional ingredients, themselves resolved). On failure a reason is
    recorded in sess and [] is returned; resolution of siblings continues
    so every reason is collected.
    """
    current = dict(ingredient)
    extras = []
    while current["name"] in excluded:
        name = current["name"]
        if name in seen:
            sess.reasons.add("substitution cycle involving " + name)
            return []
        seen = seen | {name}
        choice = None
        for candidate in ranked:
            if candidate["for"] == name:
                choice = candidate
                break
        if choice is None:
            sess.reasons.add("no substitution for " + name)
            return []
        sess.choices.append(choice["id"])
        result = choice["result"]
        replaced = dict(current)
        replaced["name"] = result["name"]
        if "quantity_factor" in result:
            replaced["quantity"] = replaced["quantity"] * result["quantity_factor"]
        if "unit" in result:
            replaced["unit"] = result["unit"]
        if "preparation" in result:
            replaced["preparation"] = result["preparation"]
        for pair in result.get("wording_changes", []):
            sess.wording.append((pair["old"], pair["new"]))
        sess.equip_add.extend(_first(result, ("equipment_added", "add_equipment"), []))
        sess.equip_remove.extend(_first(result, ("equipment_removed", "remove_equipment"), []))
        if "yield_factor" in result:
            sess.warnings.append("substitution %s changes yield" % choice["id"])
        sess.warnings.append("substituted %s with %s" % (name, result["name"]))
        for extra in _first(result, ("additional_ingredients", "additions"), []):
            scaled = dict(extra)
            scaled["quantity"] = extra["quantity"] * scale
            extras.extend(_resolve(scaled, excluded, ranked, scale, sess, seen))
        current = replaced
    return [current] + extras


def adapt(recipe, request, catalog):
    ranked = _rank(catalog)
    excluded = set(request.get("excluded") or [])
    available = set(request.get("available_equipment") or [])
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])

    sess = _Session()

    def adapt_ingredients(ingredients, session):
        out = []
        for ing in ingredients:
            scaled = dict(ing)
            scaled["quantity"] = ing["quantity"] * scale
            out.extend(_resolve(scaled, excluded, ranked, scale, session, frozenset()))
        return out

    ingredients = adapt_ingredients(recipe.get("ingredients", []), sess)

    components = []
    for comp in recipe.get("components", []):
        comp_sess = _Session()
        comp_ingredients = adapt_ingredients(comp.get("ingredients", []), comp_sess)
        missing = [e for e in comp.get("equipment", []) if e not in available]
        if (comp_sess.reasons or missing) and comp.get("optional"):
            sess.warnings.append(
                "dropped optional component " + comp.get("name", "component"))
            continue
        sess.merge(comp_sess)
        for e in missing:
            sess.reasons.add("equipment %s unavailable" % e)
        new_comp = dict(comp)
        if "ingredients" in comp:
            new_comp["ingredients"] = comp_ingredients
        if "equipment" in comp:
            new_comp["equipment"] = sorted(comp["equipment"])
        if "instructions" in comp:
            new_comp["instructions"] = list(comp["instructions"])
        components.append(new_comp)

    equipment = sorted(
        (set(recipe.get("equipment", [])) | set(sess.equip_add))
        - set(sess.equip_remove))
    for e in equipment:
        if e not in available:
            sess.reasons.add("equipment %s unavailable" % e)

    def reword(lines):
        out = []
        for line in lines:
            for old, new in sess.wording:
                line = line.replace(old, new)
            out.append(line)
        return out

    instructions = reword(recipe.get("instructions", []))
    for comp in components:
        if "instructions" in comp:
            comp["instructions"] = reword(comp["instructions"])

    possible = not sess.reasons
    adapted = None
    if possible:
        adapted = {
            "title": recipe["title"],
            "yield": target,
            "ingredients": ingredients,
            "equipment": equipment,
            "instructions": instructions,
            "components": components,
            "authored_text": recipe["authored_text"],
        }
    return {
        "possible": possible,
        "recipe": adapted,
        "choices": list(sess.choices),
        "warnings": sorted(set(sess.warnings)),
        "reasons": sorted(sess.reasons),
    }


if __name__ == "__main__":
    F = Fraction

    def ing(iid, name, qty, unit, prep=""):
        return {"id": iid, "name": name, "quantity": qty,
                "unit": unit, "preparation": prep}

    base = {
        "title": "Pancakes",
        "yield": F(3),
        "ingredients": [
            ing("i1", "milk", F(3, 2), "cup"),
            ing("i2", "butter", F(3), "tbsp", "melted"),
        ],
        "equipment": ["skillet", "whisk"],
        "instructions": ["Whisk the milk.", "Melt the butter in the skillet."],
        "components": [],
        "authored_text": "Pancakes\nServes 3.\nWhisk the milk.\n",
    }
    authored_before = print_original(base)

    catalog = [
        {"id": "c-marg", "for": "butter", "priority": 2,
         "result": {"name": "margarine"}},
        {"id": "c-oil", "for": "butter", "priority": 1,
         "result": {"name": "oil", "quantity_factor": F(3, 4), "unit": "tbsp",
                    "preparation": "",
                    "wording_changes": [
                        {"old": "Melt the butter", "new": "Pour the oil"}],
                    "equipment_added": ["measuring spoon"],
                    "equipment_removed": []}},
        {"id": "c-oat", "for": "milk", "priority": 1,
         "result": {"name": "oat milk"}},
        {"id": "c-soy", "for": "oat milk", "priority": 1,
         "result": {"name": "soy milk"}},
    ]
    request = {"target_yield": F(5),
               "available_equipment": ["measuring spoon", "skillet", "whisk"],
               "excluded": ["butter", "milk", "oat milk"]}

    out = adapt(base, request, catalog)
    assert out["possible"] and out["reasons"] == []
    adapted = out["recipe"]
    # Scaling 3 -> 5 servings, chained milk -> oat milk -> soy milk,
    # and the 3/4 replacement factor on butter -> oil.
    qty = {i["name"]: i["quantity"] for i in adapted["ingredients"]}
    assert adapted["yield"] == F(5)
    assert qty["soy milk"] == F(3, 2) * F(5, 3)
    assert qty["oil"] == F(3) * F(5, 3) * F(3, 4)
    # Substitution changed a referenced verb and the equipment set.
    assert "Pour the oil in the skillet." in adapted["instructions"]
    assert "measuring spoon" in adapted["equipment"]
    assert adapted["equipment"] == sorted(adapted["equipment"])
    assert set(adapted["equipment"]) <= set(request["available_equipment"])
    # Deterministic choice by explicit priority: c-oil (1) beats c-marg (2).
    assert out["choices"] == ["c-oat", "c-soy", "c-oil"]
    assert out["warnings"] == sorted(out["warnings"])
    # Exact original print and no input mutation after adaptation.
    assert print_original(base) == authored_before
    assert base["ingredients"][0]["quantity"] == F(3, 2)
    assert base["instructions"][1] == "Melt the butter in the skillet."

    # Exclusion reintroduced later: an additional ingredient is itself
    # excluded and is chained through the catalog.
    base2 = {
        "title": "Cake", "yield": F(1),
        "ingredients": [ing("e1", "egg", F(2), "unit")],
        "equipment": [], "instructions": [], "components": [],
        "authored_text": "Cake\n",
    }
    catalog2 = [
        {"id": "s-flax", "for": "egg", "priority": 1,
         "result": {"name": "flax meal",
                    "additional_ingredients": [ing("x1", "honey", F(1), "tbsp")]}},
        {"id": "s-maple", "for": "honey", "priority": 1,
         "result": {"name": "maple syrup"}},
    ]
    out2 = adapt(base2, {"target_yield": F(1), "available_equipment": [],
                         "excluded": ["egg", "honey"]}, catalog2)
    assert out2["possible"]
    names2 = {i["name"] for i in out2["recipe"]["ingredients"]}
    assert names2 == {"flax meal", "maple syrup"}
    assert out2["choices"] == ["s-flax", "s-maple"]

    # Substitution cycle detection.
    catalog3 = [
        {"id": "n1", "for": "nutmeg", "priority": 1, "result": {"name": "mace"}},
        {"id": "n2", "for": "mace", "priority": 1, "result": {"name": "nutmeg"}},
    ]
    base3 = dict(base2, ingredients=[ing("g1", "nutmeg", F(1), "tsp")])
    out3 = adapt(base3, {"target_yield": F(1), "available_equipment": [],
                         "excluded": ["nutmeg", "mace"]}, catalog3)
    assert not out3["possible"] and out3["recipe"] is None
    assert "substitution cycle involving nutmeg" in out3["reasons"]

    # Multiple simultaneous impossibility reasons, all reported and sorted.
    base4 = {
        "title": "Soup", "yield": F(2),
        "ingredients": [ing("c1", "cream", F(1), "cup")],
        "equipment": ["blender"], "instructions": [], "components": [],
        "authored_text": "Soup\n",
    }
    out4 = adapt(base4, {"target_yield": F(2), "available_equipment": [],
                         "excluded": ["cream"]}, [])
    assert not out4["possible"] and out4["recipe"] is None
    assert out4["reasons"] == ["equipment blender unavailable",
                               "no substitution for cream"]

    print("all tests passed")
