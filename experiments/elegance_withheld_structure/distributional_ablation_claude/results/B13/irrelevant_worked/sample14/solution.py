"""recipebook.py -- recipe adaptation with scaling, substitution chains,
dietary exclusions, and equipment checks.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; all other values are JSON-shaped.
Optional catalog-choice keys understood by this implementation:
    result: {name, quantity_factor?, unit?, preparation?, yield_factor?}
    wording_changes: [{old, new}, ...]
    equipment_add / equipment_remove: [name, ...]
    additional_ingredients: [{id?, name, quantity?, unit?, preparation?}, ...]
"""

from fractions import Fraction
import copy


def print_original(recipe):
    """Return the authored text byte-for-byte."""
    return recipe["authored_text"]


def _best_choice(name, catalog):
    """Lowest priority wins; catalog order breaks ties."""
    best_key = None
    best = None
    for index, choice in enumerate(catalog):
        if choice["for"] != name:
            continue
        key = (choice["priority"], index)
        if best_key is None or key < best_key:
            best_key = key
            best = choice
    return best


class _Sink:
    """Accumulates everything an adaptation pass produces, so failures never
    short-circuit and optional components can be resolved speculatively."""

    def __init__(self):
        self.choices = []
        self.warnings = set()
        self.reasons = set()
        self.wording = []
        self.eq_add = set()
        self.eq_remove = set()

    def merge(self, other):
        self.choices.extend(other.choices)
        self.warnings |= other.warnings
        self.reasons |= other.reasons
        self.wording.extend(other.wording)
        self.eq_add |= other.eq_add
        self.eq_remove |= other.eq_remove


def adapt(recipe, request, catalog):
    recipe = copy.deepcopy(recipe)
    excluded = set(request.get("excluded") or [])
    available = set(request.get("available_equipment") or [])
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / Fraction(recipe["yield"])

    main = _Sink()

    def resolve(ingredient, seen, sink):
        """Resolve one ingredient against the exclusions; returns the resolved
        ingredient followed by any ingredients its substitutions introduced."""
        ingredient = dict(ingredient)
        extras = []
        while ingredient["name"] in excluded:
            choice = _best_choice(ingredient["name"], catalog)
            if choice is None:
                sink.reasons.add("no substitution for %s" % ingredient["name"])
                break
            sink.choices.append(choice["id"])
            result = choice["result"]
            new_name = result["name"]
            if new_name in seen:
                sink.reasons.add("substitution cycle involving %s" % new_name)
                break
            if new_name in excluded:
                sink.warnings.add(
                    "substitution %s introduces excluded ingredient %s"
                    % (choice["id"], new_name))
            seen.add(new_name)
            ingredient["name"] = new_name
            if "quantity_factor" in result:
                ingredient["quantity"] = (
                    ingredient["quantity"] * Fraction(result["quantity_factor"]))
            if "unit" in result:
                ingredient["unit"] = result["unit"]
            if "preparation" in result:
                ingredient["preparation"] = result["preparation"]
            if "yield_factor" in result:
                sink.warnings.add(
                    "substitution %s alters yield; recipe held at target yield"
                    % choice["id"])
            for pair in choice.get("wording_changes") or []:
                sink.wording.append((pair["old"], pair["new"]))
            sink.eq_add.update(choice.get("equipment_add") or [])
            sink.eq_remove.update(choice.get("equipment_remove") or [])
            for extra in choice.get("additional_ingredients") or []:
                extra = dict(extra)
                extra.setdefault("id", extra["name"])
                extra.setdefault("unit", "")
                extra.setdefault("preparation", "")
                extra["quantity"] = Fraction(extra.get("quantity", 1)) * scale
                extras.extend(resolve(extra, set(seen), sink))
        return [ingredient] + extras

    def resolve_all(ingredients, sink):
        resolved = []
        for ingredient in ingredients:
            ingredient = dict(ingredient)
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale
            resolved.extend(resolve(ingredient, {ingredient["name"]}, sink))
        return resolved

    adapted_ingredients = resolve_all(recipe.get("ingredients") or [], main)

    adapted_components = []
    for component in recipe.get("components") or []:
        comp = dict(component)
        optional = bool(comp.get("optional"))
        sink = _Sink() if optional else main
        if comp.get("ingredients"):
            comp["ingredients"] = resolve_all(comp["ingredients"], sink)
        for item in comp.get("equipment") or []:
            if item not in available and item not in sink.eq_remove:
                sink.reasons.add("equipment %s unavailable" % item)
        if optional and sink.reasons:
            main.warnings.add(
                "omitted optional component %s" % comp.get("name", "unnamed"))
            continue
        if optional:
            main.merge(sink)
        adapted_components.append(comp)

    final_equipment = sorted(
        (set(recipe.get("equipment") or []) | main.eq_add) - main.eq_remove)
    for item in final_equipment:
        if item not in available:
            main.reasons.add("equipment %s unavailable" % item)

    if main.reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": main.choices,
            "warnings": sorted(main.warnings),
            "reasons": sorted(main.reasons),
        }

    def rewrite(lines):
        out = []
        for line in lines:
            for old, new in main.wording:
                line = line.replace(old, new)
            out.append(line)
        return out

    for comp in adapted_components:
        if "instructions" in comp:
            comp["instructions"] = rewrite(comp["instructions"])
        if "equipment" in comp:
            comp["equipment"] = sorted(set(comp["equipment"]) - main.eq_remove)

    adapted = {
        "title": recipe["title"],
        "yield": target_yield,
        "ingredients": adapted_ingredients,
        "equipment": final_equipment,
        "instructions": rewrite(recipe.get("instructions") or []),
        "components": adapted_components,
        "authored_text": recipe.get("authored_text", ""),
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": main.choices,
        "warnings": sorted(main.warnings),
        "reasons": [],
    }


if __name__ == "__main__":
    F = Fraction
    base = {
        "title": "Pancakes",
        "yield": F(3),
        "ingredients": [
            {"id": "flour", "name": "flour", "quantity": F(2), "unit": "cup",
             "preparation": "sifted"},
            {"id": "butter", "name": "butter", "quantity": F(3), "unit": "tbsp",
             "preparation": "melted"},
            {"id": "cream", "name": "cream", "quantity": F(1), "unit": "cup",
             "preparation": ""},
        ],
        "equipment": ["skillet", "whisk"],
        "instructions": ["Sift the flour.", "Melt the butter.",
                         "Whip the cream."],
        "components": [],
        "authored_text": "Pancakes\nServes 3.\n2 cup flour, sifted\n",
    }
    catalog = [
        {"id": "b2m", "for": "butter", "priority": 1,
         "result": {"name": "margarine", "quantity_factor": F(1)}},
        {"id": "m2o", "for": "margarine", "priority": 1,
         "result": {"name": "oil", "quantity_factor": F(3, 4), "unit": "tbsp"},
         "wording_changes": [{"old": "Melt the butter.",
                              "new": "Measure the oil."}]},
        {"id": "c2s", "for": "cream", "priority": 2,
         "result": {"name": "soy cream"}},
        {"id": "c2c", "for": "cream", "priority": 1,
         "result": {"name": "coconut cream", "preparation": "chilled"},
         "wording_changes": [{"old": "Whip", "new": "Chill and whip"}],
         "equipment_add": ["metal bowl"]},
    ]
    snapshot = copy.deepcopy(base)
    all_eq = ["skillet", "whisk", "metal bowl"]

    # Scaling 3 -> 5 servings.
    r = adapt(base, {"target_yield": F(5), "available_equipment": all_eq,
                     "excluded": []}, catalog)
    assert r["possible"] and r["reasons"] == []
    assert r["recipe"]["yield"] == F(5)
    assert r["recipe"]["ingredients"][0]["quantity"] == F(10, 3)

    # Chained substitution (butter -> margarine -> oil) with a reintroduced
    # exclusion warning, a verb + equipment change (cream -> coconut cream),
    # and priority beating catalog order (c2c over c2s).
    r = adapt(base, {"target_yield": F(3), "available_equipment": all_eq,
                     "excluded": ["butter", "margarine", "cream"]}, catalog)
    assert r["possible"]
    names = [i["name"] for i in r["recipe"]["ingredients"]]
    assert "oil" in names and "coconut cream" in names
    assert "butter" not in names and "margarine" not in names
    assert r["choices"] == ["b2m", "m2o", "c2c"]
    assert any("margarine" in w for w in r["warnings"])
    assert r["warnings"] == sorted(r["warnings"])
    assert "Measure the oil." in r["recipe"]["instructions"]
    assert "Chill and whip the cream." in r["recipe"]["instructions"]
    assert "metal bowl" in r["recipe"]["equipment"]
    assert r["recipe"]["equipment"] == sorted(r["recipe"]["equipment"])

    # Multiple simultaneous impossibility reasons: missing substitution,
    # substitution cycle, and unavailable equipment, all reported together.
    cyc = catalog + [{"id": "o2b", "for": "oil", "priority": 1,
                      "result": {"name": "butter"}}]
    r = adapt(base, {"target_yield": F(3), "available_equipment": ["skillet"],
                     "excluded": ["butter", "margarine", "oil", "flour"]}, cyc)
    assert not r["possible"] and r["recipe"] is None
    assert r["reasons"] == sorted(r["reasons"])
    assert "no substitution for flour" in r["reasons"]
    assert "substitution cycle involving butter" in r["reasons"]
    assert "equipment whisk unavailable" in r["reasons"]

    # Inputs never mutated; original prints byte-for-byte after adaptations.
    assert base == snapshot
    assert print_original(base) == "Pancakes\nServes 3.\n2 cup flour, sifted\n"
    print("all acceptance checks passed")
