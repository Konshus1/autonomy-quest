"""recipebook: adapt authored recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog.

Public API:
    adapt(recipe, request, catalog) -> dict
    print_original(recipe) -> str

Quantities and yields are fractions.Fraction; everything else is JSON-shaped.

Keys the public contract leaves open are fixed here.  A catalog choice's
``result`` may carry:
    equipment_add          list of equipment names the substitute requires
    equipment_remove       list of equipment names no longer needed
    additional_ingredients list of ingredient dicts (quantities stated at the
                           recipe's authored yield; they are scaled and are
                           subject to the same exclusions and catalog)
    yield_factor           Fraction multiplier on produced yield; quantities
                           are compensated so the adapted recipe still lands
                           exactly on the requested target yield
Neither function mutates its inputs.
"""

from fractions import Fraction

_ONE = Fraction(1)


def print_original(recipe):
    """Return the recipe exactly as authored, byte for byte."""
    return recipe["authored_text"]


def _ordered_candidates(catalog, name):
    """Catalog choices for ``name``, best first: lowest priority wins,
    catalog order breaks ties."""
    ranked = [(c["priority"], i, c) for i, c in enumerate(catalog) if c["for"] == name]
    ranked.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in ranked]


def _apply_wording(instructions, changes, warnings):
    for pair in changes:
        hit = False
        for i, line in enumerate(instructions):
            if pair["old"] in line:
                instructions[i] = line.replace(pair["old"], pair["new"])
                hit = True
        if not hit:
            warnings.add("wording change not applied: " + pair["old"])


def _resolve(ing, excluded, catalog, state, instructions, reasons):
    """Chain-substitute ``ing`` (a private copy) until its name is allowed.

    Returns the resolved ingredient dict, or None after recording a reason.
    A per-chain seen-set catches substitutions that reintroduce an earlier
    name (directly or through a longer cycle).
    """
    seen = {ing["name"]}
    while ing["name"] in excluded:
        candidates = _ordered_candidates(catalog, ing["name"])
        if not candidates:
            reasons.add("no substitution for " + ing["name"])
            return None
        choice = candidates[0]
        state["choices"].append(choice["id"])
        result = choice["result"]
        new_name = result["name"]
        if new_name in seen:
            reasons.add("substitution cycle involving " + new_name)
            return None

        old_name = ing["name"]
        ing["name"] = new_name
        if "quantity_factor" in result:
            ing["quantity"] = ing["quantity"] * Fraction(result["quantity_factor"])
        if "unit" in result:
            ing["unit"] = result["unit"]
        if "preparation" in result:
            ing["preparation"] = result["preparation"]
        elif ing.get("preparation"):
            ing["preparation"] = ing["preparation"].replace(old_name, new_name)

        _apply_wording(instructions, result.get("wording_changes", []), state["warnings"])
        # Any occurrence of the replaced name not covered by an explicit
        # wording change must still disappear from the final instructions.
        for i, line in enumerate(instructions):
            if old_name in line:
                instructions[i] = line.replace(old_name, new_name)

        for item in result.get("equipment_add", []):
            state["equipment"].add(item)
        for item in result.get("equipment_remove", []):
            state["equipment"].discard(item)

        if "yield_factor" in result:
            state["yield_factor"] = state["yield_factor"] * Fraction(result["yield_factor"])
            state["warnings"].add("yield adjusted by " + str(choice["id"]))

        for extra in result.get("additional_ingredients", []):
            added = dict(extra)
            added.setdefault("id", added["name"])
            added.setdefault("unit", "")
            added.setdefault("preparation", "")
            added["quantity"] = Fraction(added["quantity"]) * state["scale"]
            state["pending"].append(added)

        seen.add(new_name)
    return ing


def _drain(source_ingredients, scale, excluded, catalog, state, instructions, reasons):
    """Scale, then resolve a batch of ingredients plus anything substitutions
    add along the way.  Returns the resolved ingredient list."""
    resolved = []
    state["pending"] = [
        dict(ing, quantity=Fraction(ing["quantity"]) * scale)
        for ing in source_ingredients
    ]
    while state["pending"]:
        ing = state["pending"].pop(0)
        done = _resolve(ing, excluded, catalog, state, instructions, reasons)
        if done is not None:
            resolved.append(done)
    return resolved


def _render(title, yield_amount, ingredients, equipment, instructions, components):
    lines = [title, "Yield: " + str(yield_amount), "", "Ingredients:"]
    for ing in ingredients:
        lines.append(_render_ingredient(ing))
    if equipment:
        lines.append("Equipment: " + ", ".join(equipment))
    lines.append("")
    for i, step in enumerate(instructions, 1):
        lines.append("%d. %s" % (i, step))
    for comp in components:
        if not isinstance(comp, dict):
            lines.extend(["", str(comp)])
            continue
        lines.extend(["", "Component: " + str(comp.get("name", ""))])
        for ing in comp.get("ingredients", []):
            lines.append(_render_ingredient(ing))
        for i, step in enumerate(comp.get("instructions", []), 1):
            lines.append("%d. %s" % (i, step))
    return "\n".join(lines)


def _render_ingredient(ing):
    piece = "  " + str(ing["quantity"])
    if ing.get("unit"):
        piece += " " + ing["unit"]
    piece += " " + ing["name"]
    if ing.get("preparation"):
        piece += ", " + ing["preparation"]
    return piece


def adapt(recipe, request, catalog):
    target = Fraction(request["target_yield"])
    scale = target / Fraction(recipe["yield"])
    excluded = set(request["excluded"])
    available = set(request["available_equipment"])

    state = {
        "choices": [],
        "warnings": set(),
        "equipment": set(recipe["equipment"]),
        "yield_factor": _ONE,
        "scale": scale,
        "pending": [],
    }
    reasons = set()
    instructions = list(recipe["instructions"])

    final_ingredients = _drain(
        recipe["ingredients"], scale, excluded, catalog, state, instructions, reasons
    )

    components = []
    for comp in recipe.get("components", []):
        if not isinstance(comp, dict):
            components.append(comp)
            continue
        comp_reasons = set()
        comp_instructions = list(comp.get("instructions", []))
        comp_ingredients = _drain(
            comp.get("ingredients", []), scale, excluded, catalog,
            state, comp_instructions, comp_reasons,
        )
        if comp_reasons and comp.get("optional"):
            state["warnings"].add(
                "omitted optional component " + str(comp.get("name", ""))
            )
            continue
        reasons |= comp_reasons
        new_comp = dict(comp)
        if "ingredients" in comp:
            new_comp["ingredients"] = comp_ingredients
        if "instructions" in comp:
            new_comp["instructions"] = comp_instructions
        components.append(new_comp)

    for item in sorted(state["equipment"]):
        if item not in available:
            reasons.add("equipment " + item + " unavailable")

    warnings = sorted(state["warnings"])
    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": state["choices"],
            "warnings": warnings,
            "reasons": sorted(reasons),
        }

    # A yield-changing substitution altered how much the recipe produces;
    # compensate quantities so the result sits exactly at the target yield.
    if state["yield_factor"] != _ONE:
        compensation = _ONE / state["yield_factor"]
        for ing in final_ingredients:
            ing["quantity"] = ing["quantity"] * compensation
        for comp in components:
            if isinstance(comp, dict):
                for ing in comp.get("ingredients", []):
                    ing["quantity"] = ing["quantity"] * compensation

    equipment = sorted(state["equipment"])
    adapted = {
        "title": recipe["title"],
        "yield": target,
        "ingredients": final_ingredients,
        "equipment": equipment,
        "instructions": instructions,
        "components": components,
        "authored_text": _render(
            recipe["title"], target, final_ingredients,
            equipment, instructions, components,
        ),
    }
    return {
        "possible": True,
        "recipe": adapted,
        "choices": state["choices"],
        "warnings": warnings,
        "reasons": [],
    }
