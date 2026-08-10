"""recipebook.py

Adapt structured recipes to a target yield, available equipment, and
dietary exclusions using a prioritized substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is
JSON-shaped.  Only the Python standard library is used.

A catalog choice is {"id", "for", "priority", "result"}.  The keys
understood inside "result":
    name                    replacement ingredient name (required)
    quantity_factor         Fraction multiplier applied to the quantity
    unit                    replacement unit string
    preparation             replacement preparation string
    wording_changes         list of {"old": str, "new": str} applied to
                            instruction and preparation text
    add_equipment           equipment names the replacement requires
    remove_equipment        equipment names no longer required
    yield_factor            Fraction multiplier on the recipe's natural
                            yield; quantities are rescaled so the
                            adapted recipe still meets the target yield
    additional_ingredients  ingredient dicts authored at the original
                            yield; they are scaled and adapted like any
                            other ingredient (chaining is supported)
"""

from fractions import Fraction
import copy


def print_original(recipe):
    """Return the authored text exactly as written."""
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    """Adapt ``recipe`` to ``request`` using ``catalog``.

    Returns {"possible", "recipe", "choices", "warnings", "reasons"}.
    Neither argument is mutated.
    """
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])

    main = [_scaled_copy(ing, scale) for ing in recipe["ingredients"]]
    components = []
    for comp in recipe.get("components", []):
        comp_copy = copy.deepcopy({k: v for k, v in comp.items()
                                   if k != "ingredients"})
        comp_copy["ingredients"] = [_scaled_copy(ing, scale)
                                    for ing in comp.get("ingredients", [])]
        components.append(comp_copy)

    state = {
        "instructions": list(recipe["instructions"]),
        "equipment": set(recipe["equipment"]),
        "all": main + [ing for comp in components
                       for ing in comp["ingredients"]],
        "choices": [],
        "warnings": set(),
        "reasons": set(),
        "yield_factor": Fraction(1),
    }

    pending = [(main, ing) for ing in main]
    for comp in components:
        pending.extend((comp["ingredients"], ing)
                       for ing in comp["ingredients"])

    pos = 0
    while pos < len(pending):
        container, ing = pending[pos]
        pos += 1
        _resolve(ing, container, pending, state, excluded, catalog, scale)

    for item in sorted(state["equipment"]):
        if item not in available:
            state["reasons"].add("equipment %s unavailable" % item)

    if state["reasons"]:
        return {
            "possible": False,
            "recipe": None,
            "choices": state["choices"],
            "warnings": sorted(state["warnings"]),
            "reasons": sorted(state["reasons"]),
        }

    if state["yield_factor"] != 1:
        for ing in state["all"]:
            ing["quantity"] = ing["quantity"] / state["yield_factor"]

    adapted = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": main,
        "equipment": sorted(state["equipment"]),
        "instructions": state["instructions"],
        "components": components,
        "authored_text": recipe["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": state["choices"],
        "warnings": sorted(state["warnings"]),
        "reasons": [],
    }


def _scaled_copy(ingredient, scale):
    out = dict(ingredient)
    out["quantity"] = Fraction(ingredient["quantity"]) * scale
    return out


def _select_choice(catalog, name):
    """Lowest priority wins; catalog order breaks ties."""
    best_key = None
    best = None
    for index, choice in enumerate(catalog):
        if choice.get("for") != name:
            continue
        key = (choice["priority"], index)
        if best_key is None or key < best_key:
            best_key, best = key, choice
    return best


def _apply_wording(state, pairs):
    for pair in pairs:
        old, new = pair["old"], pair["new"]
        state["instructions"] = [text.replace(old, new)
                                 for text in state["instructions"]]
        for ing in state["all"]:
            if ing.get("preparation"):
                ing["preparation"] = ing["preparation"].replace(old, new)


def _resolve(ing, container, pending, state, excluded, catalog, scale):
    """Substitute ``ing`` until its name is no longer excluded."""
    seen = {ing["name"]}
    while ing["name"] in excluded:
        choice = _select_choice(catalog, ing["name"])
        if choice is None:
            state["reasons"].add("no substitution for %s" % ing["name"])
            return
        state["choices"].append(choice["id"])
        result = choice["result"]

        ing["name"] = result["name"]
        if "quantity_factor" in result:
            ing["quantity"] = ing["quantity"] * Fraction(result["quantity_factor"])
        if "unit" in result:
            ing["unit"] = result["unit"]
        if "preparation" in result:
            ing["preparation"] = result["preparation"]
        _apply_wording(state, result.get("wording_changes", []))
        for item in result.get("remove_equipment", []):
            state["equipment"].discard(item)
        state["equipment"].update(result.get("add_equipment", []))
        if "yield_factor" in result:
            state["yield_factor"] *= Fraction(result["yield_factor"])
            state["warnings"].add("substitution %s changed yield" % choice["id"])
        for extra in result.get("additional_ingredients", []):
            added = _scaled_copy(extra, scale)
            container.append(added)
            state["all"].append(added)
            pending.append((container, added))

        new_name = result["name"]
        if new_name in excluded:
            if new_name in seen:
                state["reasons"].add(
                    "substitution cycle involving %s" % new_name)
                return
            state["warnings"].add(
                "substitution %s reintroduced excluded ingredient %s"
                % (choice["id"], new_name))
            seen.add(new_name)


# ---------------------------------------------------------------------------
# Executable acceptance contract.

def _recipe():
    return {
        "title": "Pan Fried Tofu Bowl",
        "yield": Fraction(3),
        "ingredients": [
            {"id": "i1", "name": "tofu", "quantity": Fraction(3, 2),
             "unit": "lb", "preparation": "press the tofu"},
            {"id": "i2", "name": "butter", "quantity": Fraction(3),
             "unit": "tbsp", "preparation": "melt the butter"},
            {"id": "i3", "name": "rice", "quantity": Fraction(3, 4),
             "unit": "cup", "preparation": "rinse the rice"},
        ],
        "equipment": ["skillet", "pot"],
        "instructions": [
            "melt the butter in the skillet",
            "fry the tofu until golden",
            "simmer the rice in the pot",
        ],
        "components": [],
        "authored_text": (
            "Pan Fried Tofu Bowl\n"
            "Serves 3.\n"
            "1 1/2 lb tofu, 3 tbsp butter, 3/4 cup rice.\n"
            "Melt the butter in the skillet; fry the tofu; simmer the rice.\n"
        ),
    }


def _request(**overrides):
    request = {
        "target_yield": Fraction(3),
        "available_equipment": ["pot", "saucepan", "skillet"],
        "excluded": [],
    }
    request.update(overrides)
    return request


def _quantities(out):
    return {ing["name"]: ing["quantity"]
            for ing in out["recipe"]["ingredients"]}


def _self_test():
    # Scaling 3 -> 5 servings.
    out = adapt(_recipe(), _request(target_yield=Fraction(5)), [])
    assert out["possible"] and out["reasons"] == []
    assert out["recipe"]["yield"] == Fraction(5)
    assert _quantities(out) == {"tofu": Fraction(5, 2), "butter": Fraction(5),
                                "rice": Fraction(5, 4)}

    # Chained substitution; the exclusion reintroduced on the way is
    # detected, warned about, and substituted again.
    chain = [
        {"id": "butter->margarine", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "margarine->oil", "for": "margarine", "priority": 1,
         "result": {"name": "olive oil", "quantity_factor": Fraction(3, 4),
                    "preparation": "measure the olive oil",
                    "wording_changes": [{"old": "melt the butter",
                                         "new": "heat the olive oil"}]}},
    ]
    out = adapt(_recipe(), _request(excluded=["butter", "margarine"]), chain)
    assert out["possible"]
    assert out["choices"] == ["butter->margarine", "margarine->oil"]
    assert _quantities(out)["olive oil"] == Fraction(9, 4)
    assert out["recipe"]["instructions"][0] == "heat the olive oil in the skillet"
    assert out["warnings"] == [
        "substitution butter->margarine reintroduced excluded ingredient margarine"]
    assert not any(n in ("butter", "margarine") for n in _quantities(out))

    # Substitution changing a referenced verb and the equipment list.
    verb = [{"id": "butter->oil", "for": "butter", "priority": 1,
             "result": {"name": "olive oil",
                        "preparation": "pour the olive oil",
                        "wording_changes": [
                            {"old": "melt the butter in the skillet",
                             "new": "heat the olive oil in the saucepan"}],
                        "remove_equipment": ["skillet"],
                        "add_equipment": ["saucepan"]}}]
    out = adapt(_recipe(), _request(excluded=["butter"]), verb)
    assert out["possible"]
    assert out["recipe"]["instructions"][0] == "heat the olive oil in the saucepan"
    assert out["recipe"]["equipment"] == ["pot", "saucepan"]

    # An additional ingredient that is itself excluded is chained too.
    addition = [
        {"id": "butter->oil+salt", "for": "butter", "priority": 1,
         "result": {"name": "olive oil",
                    "additional_ingredients": [
                        {"id": "x1", "name": "salt",
                         "quantity": Fraction(1, 2), "unit": "tsp",
                         "preparation": "stir in the salt"}]}},
        {"id": "salt->miso", "for": "salt", "priority": 1,
         "result": {"name": "miso"}},
    ]
    out = adapt(_recipe(), _request(excluded=["butter", "salt"]), addition)
    assert out["possible"]
    assert out["choices"] == ["butter->oil+salt", "salt->miso"]
    assert _quantities(out)["miso"] == Fraction(1, 2)

    # A substitution that changes the recipe's natural yield still lands
    # exactly on the target yield.
    yielding = [{"id": "butter->oat", "for": "butter", "priority": 1,
                 "result": {"name": "oat cream",
                            "yield_factor": Fraction(3, 2)}}]
    out = adapt(_recipe(), _request(excluded=["butter"]), yielding)
    assert out["possible"] and out["recipe"]["yield"] == Fraction(3)
    assert _quantities(out) == {"tofu": Fraction(1), "oat cream": Fraction(2),
                                "rice": Fraction(1, 2)}
    assert out["warnings"] == ["substitution butter->oat changed yield"]

    # Every independently applicable impossibility reason is reported.
    out = adapt(_recipe(),
                _request(available_equipment=["skillet"], excluded=["tofu"]),
                [])
    assert not out["possible"] and out["recipe"] is None
    assert out["reasons"] == ["equipment pot unavailable",
                              "no substitution for tofu"]

    # Substitution cycles are impossible, naming the recurring ingredient.
    cyclic = [
        {"id": "b->m", "for": "butter", "priority": 1,
         "result": {"name": "margarine"}},
        {"id": "m->b", "for": "margarine", "priority": 1,
         "result": {"name": "butter"}},
    ]
    out = adapt(_recipe(), _request(excluded=["butter", "margarine"]), cyclic)
    assert not out["possible"]
    assert out["reasons"] == ["substitution cycle involving butter"]

    # Deterministic choice: lowest priority wins, catalog order breaks ties.
    ranked = [
        {"id": "late-low", "for": "butter", "priority": 2,
         "result": {"name": "ghee"}},
        {"id": "winner", "for": "butter", "priority": 1,
         "result": {"name": "olive oil"}},
        {"id": "tie-loser", "for": "butter", "priority": 1,
         "result": {"name": "coconut oil"}},
    ]
    out = adapt(_recipe(), _request(excluded=["butter"]), ranked)
    assert out["choices"] == ["winner"]
    assert "olive oil" in _quantities(out)

    # The original stays printable byte-for-byte and is never mutated.
    original = _recipe()
    snapshot = copy.deepcopy(original)
    adapt(original, _request(target_yield=Fraction(5), excluded=["butter"]),
          chain)
    adapt(original, _request(available_equipment=[], excluded=["tofu"]), [])
    assert original == snapshot
    assert print_original(original) == snapshot["authored_text"]

    print("all checks passed")


if __name__ == "__main__":
    _self_test()
