"""Recipe adaptation using only the Python standard library."""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the original authored text without interpreting or modifying it."""
    return recipe["authored_text"]


def _key(value):
    return str(value).casefold()


def _empty_outcome():
    return {
        "ingredients": [],
        "choices": [],
        "wording": [],
        "equipment_ops": [],
        "yield_ops": [],
        "reasons": [],
    }


def _combine(left, right):
    combined = []
    for first in left:
        for second in right:
            combined.append({
                "ingredients": first["ingredients"] + second["ingredients"],
                "choices": first["choices"] + second["choices"],
                "wording": first["wording"] + second["wording"],
                "equipment_ops": (
                    first["equipment_ops"] + second["equipment_ops"]
                ),
                "yield_ops": first["yield_ops"] + second["yield_ops"],
                "reasons": first["reasons"] + second["reasons"],
            })
    return combined


def _index_catalog(catalog):
    indexed = {}
    for position, choice in enumerate(catalog):
        indexed.setdefault(_key(choice["for"]), []).append(
            (choice.get("priority", 0), position, choice)
        )

    for choices in indexed.values():
        choices.sort(key=lambda entry: (entry[0], entry[1]))
    return indexed


def _wording_changes(result):
    changes = []
    for change in result.get("wording_changes", ()) or ():
        if isinstance(change, dict):
            changes.append((str(change["old"]), str(change["new"])))
        else:
            old, new = change
            changes.append((str(old), str(new)))
    return changes


def _equipment_operations(result):
    additions = result.get(
        "equipment_additions", result.get("add_equipment", ())
    )
    removals = result.get(
        "equipment_removals", result.get("remove_equipment", ())
    )

    equipment = result.get("equipment")
    if isinstance(equipment, dict):
        additions = equipment.get("add", additions)
        removals = equipment.get("remove", removals)

    operations = [("remove", item) for item in removals or ()]
    operations.extend(("add", item) for item in additions or ())
    return operations


def _yield_operation(result):
    if "yield_factor" in result:
        return "factor", Fraction(result["yield_factor"])
    if "yield" in result:
        return "absolute", Fraction(result["yield"])
    if "yield_change" in result:
        return "factor", Fraction(result["yield_change"])
    return None


def _resolve_ingredient(ingredient, excluded, catalog, trail=()):
    name = ingredient["name"]
    normalized = _key(name)

    if normalized not in excluded:
        outcome = _empty_outcome()
        outcome["ingredients"] = [deepcopy(ingredient)]
        return [outcome]

    if normalized in trail:
        outcome = _empty_outcome()
        outcome["reasons"] = [
            "substitution cycle involving " + str(name)
        ]
        return [outcome]

    candidates = catalog.get(normalized, ())
    if not candidates:
        outcome = _empty_outcome()
        outcome["reasons"] = ["no substitution for " + str(name)]
        return [outcome]

    outcomes = []
    next_trail = trail + (normalized,)

    for _priority, _position, choice in candidates:
        result = choice.get("result", {})
        replacement = deepcopy(ingredient)
        replacement["name"] = result["name"]
        replacement["quantity"] = (
            Fraction(replacement["quantity"])
            * Fraction(result.get("quantity_factor", 1))
        )

        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        branch = _resolve_ingredient(
            replacement, excluded, catalog, next_trail
        )

        for additional in result.get("additional_ingredients", ()) or ():
            branch = _combine(
                branch,
                _resolve_ingredient(
                    deepcopy(additional), excluded, catalog, next_trail
                ),
            )

        wording = _wording_changes(result)
        equipment_ops = _equipment_operations(result)
        yield_op = _yield_operation(result)

        for branch_outcome in branch:
            outcome = deepcopy(branch_outcome)
            outcome["choices"].insert(0, choice["id"])
            outcome["wording"] = wording + outcome["wording"]
            outcome["equipment_ops"] = (
                equipment_ops + outcome["equipment_ops"]
            )
            if yield_op is not None:
                outcome["yield_ops"].insert(0, yield_op)
            outcomes.append(outcome)

    return outcomes


def _ingredient_lists(recipe):
    lists = []

    def visit(node):
        if not isinstance(node, dict):
            return

        ingredients = node.get("ingredients")
        if isinstance(ingredients, list):
            lists.append(ingredients)

        components = node.get("components", ()) or ()
        if isinstance(components, dict):
            components = components.values()
        for component in components:
            visit(component)

    visit(recipe)
    return lists


def _edit_instructions(node, changes):
    if not isinstance(node, dict):
        return

    instructions = node.get("instructions")
    if isinstance(instructions, list):
        edited = []
        for instruction in instructions:
            text = instruction
            for old, new in changes:
                text = text.replace(old, new)
            edited.append(text)
        node["instructions"] = edited

    components = node.get("components", ()) or ()
    if isinstance(components, dict):
        components = components.values()
    for component in components:
        _edit_instructions(component, changes)


def _effective_yield(original_yield, operations):
    value = Fraction(original_yield)
    for operation, operand in operations:
        if operation == "absolute":
            value = operand
        else:
            value *= operand
    return value


def adapt(recipe, request, catalog):
    """Adapt a recipe without mutating the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    excluded = {_key(name) for name in request.get("excluded", ())}
    available = set(request.get("available_equipment", ()))
    indexed_catalog = _index_catalog(catalog)

    locations = _ingredient_lists(adapted)
    states = [(_empty_outcome(), [])]

    for ingredients in locations:
        local_outcomes = [_empty_outcome()]
        for ingredient in ingredients:
            local_outcomes = _combine(
                local_outcomes,
                _resolve_ingredient(
                    ingredient, excluded, indexed_catalog
                ),
            )

        next_states = []
        for state, replacements in states:
            for local in local_outcomes:
                merged = _combine([state], [local])[0]
                next_states.append(
                    (merged, replacements + [local["ingredients"]])
                )
        states = next_states

    evaluated = []
    original_equipment = set(adapted.get("equipment", ()) or ())

    for state, replacements in states:
        equipment = set(original_equipment)
        for operation, item in state["equipment_ops"]:
            if operation == "remove":
                equipment.discard(item)
            else:
                equipment.add(item)

        reasons = list(state["reasons"])
        for item in sorted(equipment - available, key=str):
            reasons.append("equipment " + str(item) + " unavailable")

        evaluated.append(
            (state, replacements, equipment, sorted(set(reasons)))
        )

    feasible = next(
        (evaluation for evaluation in evaluated if not evaluation[3]),
        None,
    )

    if feasible is None:
        preferred_state = evaluated[0][0]
        all_reasons = sorted({
            reason
            for _state, _replacements, _equipment, reasons in evaluated
            for reason in reasons
        })
        return {
            "possible": False,
            "recipe": None,
            "choices": preferred_state["choices"],
            "warnings": [],
            "reasons": all_reasons,
        }

    state, replacements, equipment, _reasons = feasible

    for location, replacement in zip(locations, replacements):
        location[:] = replacement

    effective_yield = _effective_yield(
        adapted["yield"], state["yield_ops"]
    )
    scale = Fraction(request["target_yield"]) / effective_yield

    for ingredients in _ingredient_lists(adapted):
        for ingredient in ingredients:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"]) * scale
            )

    adapted["yield"] = Fraction(request["target_yield"])
    adapted["equipment"] = sorted(equipment, key=str)
    _edit_instructions(adapted, state["wording"])

    return {
        "possible": True,
        "recipe": adapted,
        "choices": state["choices"],
        "warnings": [],
        "reasons": [],
    }
