"""recipebook: adapt recipes to a target yield, available equipment, and
dietary exclusions using a substitution catalog.

Quantities and yields are fractions.Fraction; everything else is
JSON-shaped. Lower numeric catalog priority wins, catalog order breaks
ties. adapt() never mutates its inputs; print_original() returns the
authored text byte-for-byte. Standard library only.
"""

import copy
from fractions import Fraction


def print_original(recipe):
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    work = copy.deepcopy(recipe)
    target = Fraction(request["target_yield"])
    available = set(request["available_equipment"])
    excluded = set(request["excluded"])
    scale = target / Fraction(work["yield"])

    choices = []
    warnings = []
    reasons = set()

    by_name = {}
    for index, entry in enumerate(catalog):
        by_name.setdefault(entry["for"], []).append((entry["priority"], index, entry))
    for options in by_name.values():
        options.sort(key=lambda item: (item[0], item[1]))

    equipment = list(work.get("equipment", []))
    wording = []
    yield_factor = Fraction(1)

    def substitute(ing, local_reasons):
        # Resolve the exclusion chain for one ingredient in place.
        # Returns newly introduced ingredients, or None if the chain fails.
        nonlocal yield_factor
        extras = []
        seen = set()
        while ing["name"] in excluded:
            name = ing["name"]
            if name in seen:
                local_reasons.add("substitution cycle involving %s" % name)
                return None
            seen.add(name)
            options = by_name.get(name)
            if not options:
                local_reasons.add("no substitution for %s" % name)
                return None
            entry = options[0][2]
            result = entry["result"]
            choices.append(entry["id"])
            wording.append((name, result["name"]))
            ing["name"] = result["name"]
            if "quantity_factor" in result:
                ing["quantity"] = ing["quantity"] * Fraction(result["quantity_factor"])
            if "unit" in result:
                ing["unit"] = result["unit"]
            if "preparation" in result:
                ing["preparation"] = result["preparation"]
            for pair in result.get("wording_changes", []):
                wording.append((pair["old"], pair["new"]))
            for item in result.get("remove_equipment", []):
                if item in equipment:
                    equipment.remove(item)
            for item in result.get("add_equipment", []):
                if item not in equipment:
                    equipment.append(item)
            if "yield_factor" in result:
                yield_factor *= Fraction(result["yield_factor"])
            for extra in result.get("additional_ingredients", []):
                extra = copy.deepcopy(extra)
                extra["quantity"] = Fraction(extra["quantity"]) * scale
                extra.setdefault("id", extra["name"])
                extra.setdefault("unit", "")
                extra.setdefault("preparation", "")
                extras.append(extra)
        return extras

    def process(ingredients, local_reasons):
        queue = []
        for ing in ingredients:
            ing["quantity"] = Fraction(ing["quantity"]) * scale
            queue.append(ing)
        done = []
        i = 0
        while i < len(queue):
            ing = queue[i]
            i += 1
            extras = substitute(ing, local_reasons)
            if extras is None:
                continue
            done.append(ing)
            queue.extend(extras)
        return done

    main_ingredients = process(work.get("ingredients", []), reasons)

    components = []
    for component in work.get("components", []):
        snapshot = (list(equipment), len(wording), yield_factor, len(choices))
        local = set()
        component["ingredients"] = process(component.get("ingredients", []), local)
        if local and component.get("optional"):
            equipment[:] = snapshot[0]
            del wording[snapshot[1]:]
            yield_factor = snapshot[2]
            del choices[snapshot[3]:]
            warnings.append("omitted optional component %s" % component["name"])
            continue
        reasons.update(local)
        components.append(component)

    for item in equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    if yield_factor != 1:
        for ing in main_ingredients:
            ing["quantity"] = ing["quantity"] / yield_factor
        for component in components:
            for ing in component["ingredients"]:
                ing["quantity"] = ing["quantity"] / yield_factor
        warnings.append("rescaled quantities to preserve target yield")

    def rewrite(lines):
        out = []
        for line in lines:
            for old, new in wording:
                line = line.replace(old, new)
            out.append(line)
        return out

    for component in components:
        if "instructions" in component:
            component["instructions"] = rewrite(component["instructions"])

    return {
        "possible": True,
        "recipe": {
            "title": work["title"],
            "yield": target,
            "ingredients": main_ingredients,
            "equipment": sorted(equipment),
            "instructions": rewrite(work.get("instructions", [])),
            "components": components,
            "authored_text": work["authored_text"],
        },
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }


if __name__ == "__main__":
    from fractions import Fraction as F

    def base():
        return {
            "title": "Pancakes",
            "yield": F(3),
            "ingredients": [
                {"id": "flour", "name": "flour", "quantity": F(3, 2), "unit": "cup", "preparation": "sifted"},
                {"id": "butter", "name": "butter", "quantity": F(3), "unit": "tbsp", "preparation": "melted"},
                {"id": "milk", "name": "milk", "quantity": F(1), "unit": "cup", "preparation": ""},
            ],
            "equipment": ["skillet", "whisk"],
            "instructions": ["Whisk the milk with the butter.", "Cook on the skillet."],
            "components": [],
            "authored_text": "Pancakes\nServes 3\n1 1/2 cup flour, sifted\n",
        }

    everything = ["skillet", "whisk", "spatula", "blender"]

    # 1. Scaling 3 -> 5 servings.
    out = adapt(base(), {"target_yield": F(5), "available_equipment": everything, "excluded": []}, [])
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == F(5)
    assert out["recipe"]["ingredients"][0]["quantity"] == F(5, 2)
    assert out["recipe"]["ingredients"][1]["quantity"] == F(5)
    assert out["recipe"]["ingredients"][2]["quantity"] == F(5, 3)

    # 2. Chained substitution: butter -> margarine -> oil with a quantity factor.
    catalog = [
        {"id": "b2m", "for": "butter", "priority": 1, "result": {"name": "margarine"}},
        {"id": "m2o", "for": "margarine", "priority": 1,
         "result": {"name": "oil", "quantity_factor": F(3, 4), "unit": "tbsp"}},
    ]
    out = adapt(base(), {"target_yield": F(3), "available_equipment": everything,
                         "excluded": ["butter", "margarine"]}, catalog)
    assert out["possible"] and out["choices"] == ["b2m", "m2o"]
    assert out["recipe"]["ingredients"][1]["name"] == "oil"
    assert out["recipe"]["ingredients"][1]["quantity"] == F(9, 4)

    # 3. Substitution changing a referenced verb and equipment.
    catalog = [
        {"id": "b2o", "for": "butter", "priority": 1,
         "result": {"name": "oil",
                    "wording_changes": [{"old": "Whisk", "new": "Stir"}],
                    "remove_equipment": ["whisk"],
                    "add_equipment": ["spatula"]}},
    ]
    out = adapt(base(), {"target_yield": F(3), "available_equipment": ["skillet", "spatula"],
                         "excluded": ["butter"]}, catalog)
    assert out["possible"]
    assert out["recipe"]["instructions"][0] == "Stir the milk with the oil."
    assert out["recipe"]["equipment"] == ["skillet", "spatula"]

    # 4a. Exclusion reintroduced later with no further substitution.
    out = adapt(base(), {"target_yield": F(3), "available_equipment": everything,
                         "excluded": ["butter", "margarine"]},
                [{"id": "b2m", "for": "butter", "priority": 1, "result": {"name": "margarine"}}])
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["no substitution for margarine"]

    # 4b. Substitution cycle.
    out = adapt(base(), {"target_yield": F(3), "available_equipment": everything,
                         "excluded": ["butter", "margarine"]},
                [{"id": "b2m", "for": "butter", "priority": 1, "result": {"name": "margarine"}},
                 {"id": "m2b", "for": "margarine", "priority": 1, "result": {"name": "butter"}}])
    assert not out["possible"]
    assert out["reasons"] == ["substitution cycle involving butter"]

    # 5. Multiple simultaneous impossibility reasons, sorted.
    out = adapt(base(), {"target_yield": F(3), "available_equipment": ["whisk"],
                         "excluded": ["milk"]}, [])
    assert not out["possible"]
    assert out["reasons"] == ["equipment skillet unavailable", "no substitution for milk"]

    # 6. Inputs untouched; original prints byte-for-byte after adaptations.
    original = base()
    pristine = copy.deepcopy(original)
    adapt(original, {"target_yield": F(5), "available_equipment": ["whisk"],
                     "excluded": ["milk"]}, [])
    adapt(original, {"target_yield": F(7, 2), "available_equipment": everything,
                     "excluded": []}, [])
    assert original == pristine
    assert print_original(original) == pristine["authored_text"]

    # 7. Deterministic choice: explicit priority wins, catalog order breaks ties.
    catalog = [
        {"id": "late", "for": "butter", "priority": 2, "result": {"name": "oil"}},
        {"id": "best", "for": "butter", "priority": 1, "result": {"name": "ghee"}},
        {"id": "tied", "for": "butter", "priority": 1, "result": {"name": "lard"}},
    ]
    out = adapt(base(), {"target_yield": F(3), "available_equipment": everything,
                         "excluded": ["butter"]}, catalog)
    assert out["choices"] == ["best"]
    assert out["recipe"]["ingredients"][1]["name"] == "ghee"

    # Additional ingredients face the same exclusions and catalog choices.
    catalog = [
        {"id": "b2s", "for": "butter", "priority": 1,
         "result": {"name": "shortening",
                    "additional_ingredients": [
                        {"id": "salt", "name": "salt", "quantity": F(1, 2), "unit": "tsp"}]}},
        {"id": "s2k", "for": "salt", "priority": 1, "result": {"name": "kelp flakes"}},
    ]
    out = adapt(base(), {"target_yield": F(3), "available_equipment": everything,
                         "excluded": ["butter", "salt"]}, catalog)
    assert out["possible"] and out["choices"] == ["b2s", "s2k"]
    names = [i["name"] for i in out["recipe"]["ingredients"]]
    assert "kelp flakes" in names and "salt" not in names and "butter" not in names

    print("all acceptance checks passed")
