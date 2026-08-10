"""recipebook.py

Adapt authored recipes to a target yield, available equipment, dietary
exclusions, and a substitution catalog, using exact rational arithmetic.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str
"""

import copy
from fractions import Fraction

_EQ_ADD_KEYS = ("add_equipment", "equipment_additions", "equipment_add")
_EQ_REMOVE_KEYS = ("remove_equipment", "equipment_removals", "equipment_remove")
_EXTRA_KEYS = ("additional_ingredients", "add_ingredients")


def print_original(recipe):
    """Return the authored text byte-for-byte; never mutates the recipe."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    recipe = copy.deepcopy(recipe)
    request = copy.deepcopy(request)
    catalog = copy.deepcopy(catalog)

    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])
    order = sorted(range(len(catalog)),
                   key=lambda i: (catalog[i]["priority"], i))
    excluded = set(request.get("excluded") or [])
    state = _State()

    ingredients = _adapt_ingredients(recipe.get("ingredients") or [],
                                     scale, excluded, catalog, order, state)

    components = []
    for comp in recipe.get("components") or []:
        comp = dict(comp)
        if comp.get("ingredients"):
            comp["ingredients"] = _adapt_ingredients(
                comp["ingredients"], scale, excluded, catalog, order, state)
        components.append(comp)

    equipment = set(recipe.get("equipment") or [])
    for op, name in state.eq_edits:
        (equipment.add if op == "add" else equipment.discard)(name)
    for name in equipment - set(request.get("available_equipment") or []):
        state.reasons.add("equipment " + name + " unavailable")

    instructions = _reword(recipe.get("instructions") or [], state.wording)
    for comp in components:
        if comp.get("instructions"):
            comp["instructions"] = _reword(comp["instructions"], state.wording)

    if state.reasons:
        adapted = None
    else:
        adapted = {
            "title": recipe["title"],
            "yield": target,
            "ingredients": ingredients,
            "equipment": sorted(equipment),
            "instructions": instructions,
            "components": components,
            "authored_text": recipe.get("authored_text", ""),
        }
    return {
        "possible": not state.reasons,
        "recipe": adapted,
        "choices": state.choices,
        "warnings": sorted(state.warnings),
        "reasons": sorted(state.reasons),
    }


class _State:
    """Accumulates cross-cutting effects of applied catalog choices."""

    def __init__(self):
        self.choices = []      # catalog ids in application order
        self.warnings = set()
        self.reasons = set()
        self.wording = []      # (old, new) pairs in application order
        self.eq_edits = []     # ("add" | "remove", equipment name)


def _adapt_ingredients(ingredients, scale, excluded, catalog, order, state):
    # Queue items pair an ingredient with the names already substituted on
    # its chain; additional ingredients inherit the spawning chain so that
    # indirect reintroduction loops are caught as cycles.
    queue = [(ing, frozenset()) for ing in ingredients]
    out = []
    while queue:
        raw, seen = queue.pop(0)
        adapted, extras = _substitute(raw, seen, scale, excluded,
                                      catalog, order, state)
        queue.extend(extras)
        if adapted is not None:
            out.append(adapted)
    return out


def _substitute(raw, seen, scale, excluded, catalog, order, state):
    ing = {
        "id": raw.get("id", raw["name"]),
        "name": raw["name"],
        "quantity": Fraction(raw["quantity"]) * scale,
        "unit": raw.get("unit"),
        "preparation": raw.get("preparation"),
    }
    seen = set(seen)
    extras = []
    while ing["name"] in excluded:
        name = ing["name"]
        if name in seen:
            state.reasons.add("substitution cycle involving " + name)
            return None, extras
        seen.add(name)
        choice = _pick(catalog, order, name)
        if choice is None:
            state.reasons.add("no substitution for " + name)
            return None, extras
        state.choices.append(choice["id"])
        result = choice["result"]
        ing["name"] = result["name"]
        if result.get("quantity_factor") is not None:
            ing["quantity"] *= Fraction(result["quantity_factor"])
        if "unit" in result:
            ing["unit"] = result["unit"]
        if "preparation" in result:
            ing["preparation"] = result["preparation"]
        for pair in result.get("wording_changes") or []:
            state.wording.append((pair["old"], pair["new"]))
        for eq in _collect(result, _EQ_ADD_KEYS):
            state.eq_edits.append(("add", eq))
        for eq in _collect(result, _EQ_REMOVE_KEYS):
            state.eq_edits.append(("remove", eq))
        for extra in _collect(result, _EXTRA_KEYS):
            extras.append((extra, frozenset(seen)))
            if extra["name"] in excluded:
                state.warnings.add("excluded ingredient " + extra["name"]
                                   + " reintroduced by " + choice["id"])
        if result.get("yield_factor") not in (None, 1, Fraction(1)):
            state.warnings.add("yield affected by " + choice["id"])
        if ing["name"] in excluded:
            state.warnings.add("excluded ingredient " + ing["name"]
                               + " reintroduced by " + choice["id"])
    return ing, extras


def _pick(catalog, order, name):
    for i in order:
        if catalog[i]["for"] == name:
            return catalog[i]
    return None


def _collect(mapping, keys):
    found = []
    for key in keys:
        found.extend(mapping.get(key) or [])
    return found


def _reword(lines, wording):
    lines = list(lines)
    for old, new in wording:
        lines = [line.replace(old, new) for line in lines]
    return lines


if __name__ == "__main__":
    base = {
        "title": "Pancakes",
        "yield": Fraction(3),
        "ingredients": [
            {"id": "i1", "name": "milk", "quantity": Fraction(3, 2),
             "unit": "cup", "preparation": "warmed"},
            {"id": "i2", "name": "butter", "quantity": Fraction(3),
             "unit": "tbsp", "preparation": "melted"},
        ],
        "equipment": ["skillet", "whisk"],
        "instructions": ["Melt the butter in the skillet.",
                         "Whisk in the warmed milk."],
        "components": [],
        "authored_text": "Pancakes\nServes 3.\n1 1/2 cup milk, warmed\n"
                         "3 tbsp butter, melted\n",
    }
    catalog = [
        {"id": "milk-oat", "for": "milk", "priority": 2,
         "result": {"name": "oat milk"}},
        {"id": "milk-soy", "for": "milk", "priority": 1,
         "result": {"name": "soy milk"}},
        {"id": "butter-marg", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "marg-oil", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "quantity_factor": Fraction(3, 4),
                    "unit": "tbsp", "preparation": "poured",
                    "wording_changes": [
                        {"old": "Melt the butter in the skillet",
                         "new": "Pour the olive oil into the saucepan"}],
                    "add_equipment": ["saucepan"],
                    "remove_equipment": ["skillet"]}},
        {"id": "wal-pec", "for": "walnuts", "priority": 1,
         "result": {"name": "pecans"}},
        {"id": "pec-wal", "for": "pecans", "priority": 1,
         "result": {"name": "walnuts"}},
    ]
    snapshot = copy.deepcopy(base)

    # Scaling 3 -> 5 with a chained substitution that changes a referenced
    # verb and swaps equipment.
    out = adapt(base, {"target_yield": Fraction(5),
                       "available_equipment": ["saucepan", "whisk"],
                       "excluded": ["butter", "margarine"]}, catalog)
    assert out["possible"] and out["reasons"] == []
    r = out["recipe"]
    assert r["yield"] == Fraction(5)
    assert r["ingredients"][0]["quantity"] == Fraction(5, 2)
    oil = r["ingredients"][1]
    assert oil["name"] == "olive oil"
    assert oil["quantity"] == Fraction(15, 4)
    assert oil["unit"] == "tbsp" and oil["preparation"] == "poured"
    assert out["choices"] == ["butter-marg", "marg-oil"]
    assert r["instructions"][0] == "Pour the olive oil into the saucepan."
    assert r["equipment"] == ["saucepan", "whisk"]
    assert ("excluded ingredient margarine reintroduced by butter-marg"
            in out["warnings"])

    # Deterministic choice: lower priority wins over catalog order.
    out2 = adapt(base, {"target_yield": Fraction(3),
                        "available_equipment": ["skillet", "whisk"],
                        "excluded": ["milk"]}, catalog)
    assert out2["choices"] == ["milk-soy"]
    assert out2["recipe"]["ingredients"][0]["name"] == "soy milk"

    # Multiple simultaneous impossibility reasons, including a cycle.
    cyc = copy.deepcopy(base)
    cyc["ingredients"] += [
        {"id": "i3", "name": "walnuts", "quantity": Fraction(1),
         "unit": "cup", "preparation": "chopped"},
        {"id": "i4", "name": "honey", "quantity": Fraction(1, 4),
         "unit": "cup", "preparation": "drizzled"},
    ]
    out3 = adapt(cyc, {"target_yield": Fraction(3),
                       "available_equipment": ["whisk"],
                       "excluded": ["walnuts", "pecans", "honey"]}, catalog)
    assert not out3["possible"] and out3["recipe"] is None
    assert out3["reasons"] == ["equipment skillet unavailable",
                               "no substitution for honey",
                               "substitution cycle involving walnuts"]

    # Originals stay printable exactly as authored; inputs never mutated.
    assert print_original(base) == snapshot["authored_text"]
    assert base == snapshot
    print("all recipebook self-tests passed")
