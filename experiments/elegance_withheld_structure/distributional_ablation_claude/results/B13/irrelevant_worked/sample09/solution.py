"""recipebook: adapt authored recipes to a target yield, available equipment,
dietary exclusions, and a substitution catalog, without mutating inputs."""

from fractions import Fraction


def print_original(recipe):
    """Return the recipe exactly as authored."""
    return recipe["authored_text"]


def _best_choice(catalog, name):
    """Lowest numeric priority wins; catalog order breaks ties."""
    best = None
    best_key = None
    for index, choice in enumerate(catalog):
        if choice.get("for") != name:
            continue
        key = (choice.get("priority", 0), index)
        if best_key is None or key < best_key:
            best, best_key = choice, key
    return best


def _equipment_changes(result):
    added = list(result.get("equipment_add", result.get("add_equipment", [])))
    removed = list(result.get("equipment_remove", result.get("remove_equipment", [])))
    nested = result.get("equipment")
    if isinstance(nested, dict):
        added += list(nested.get("add", []))
        removed += list(nested.get("remove", []))
    return added, removed


def _additional_ingredients(result):
    for key in ("additional_ingredients", "add_ingredients", "additional"):
        if key in result:
            return list(result[key])
    return []


def adapt(recipe, request, catalog):
    target_yield = Fraction(request["target_yield"])
    scale = target_yield / Fraction(recipe["yield"])
    excluded = set(request.get("excluded", []))
    available = set(request.get("available_equipment", []))

    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    equipment = list(recipe.get("equipment", []))
    yield_factor = [Fraction(1)]
    # Additional ingredients can keep introducing excluded names; bound the
    # total number of substitution applications so a malformed catalog cannot
    # loop forever, and report the overflow as a cycle.
    budget = [10 * (len(catalog) + len(recipe.get("ingredients", [])) + 5)]

    def resolve(ingredient, pending):
        current = {
            "id": ingredient.get("id", ingredient["name"]),
            "name": ingredient["name"],
            "quantity": Fraction(ingredient["quantity"]) * scale,
            "unit": ingredient.get("unit"),
            "preparation": ingredient.get("preparation"),
        }
        seen = {current["name"]}
        while current["name"] in excluded:
            if budget[0] <= 0:
                reasons.add("substitution cycle involving %s" % current["name"])
                return None
            budget[0] -= 1
            choice = _best_choice(catalog, current["name"])
            if choice is None:
                reasons.add("no substitution for %s" % current["name"])
                return None
            choices.append(choice["id"])
            result = choice["result"]
            new_name = result["name"]
            if new_name in seen:
                reasons.add("substitution cycle involving %s" % new_name)
                return None
            seen.add(new_name)
            warnings.add("substituted %s with %s" % (current["name"], new_name))
            if "quantity_factor" in result:
                current["quantity"] *= Fraction(result["quantity_factor"])
            if "unit" in result:
                current["unit"] = result["unit"]
            if "preparation" in result:
                current["preparation"] = result["preparation"]
            if "yield_factor" in result:
                yield_factor[0] *= Fraction(result["yield_factor"])
            for change in result.get("wording_changes", []):
                wording_changes.append((change["old"], change["new"]))
            added, removed = _equipment_changes(result)
            for item in removed:
                while item in equipment:
                    equipment.remove(item)
                warnings.add("equipment removed: %s" % item)
            for item in added:
                if item not in equipment:
                    equipment.append(item)
                warnings.add("equipment added: %s" % item)
            for extra in _additional_ingredients(result):
                warnings.add("added ingredient %s" % extra["name"])
                pending.append(extra)
            current["name"] = new_name
        return current

    def process_all(source_ingredients):
        done = []
        pending = list(source_ingredients)
        while pending:
            adapted = resolve(pending.pop(0), pending)
            if adapted is not None:
                done.append(adapted)
        return done

    adapted_ingredients = process_all(recipe.get("ingredients", []))

    adapted_components = []
    for component in recipe.get("components", []):
        new_component = dict(component)
        if "ingredients" in component:
            new_component["ingredients"] = process_all(component["ingredients"])
        if "instructions" in component:
            new_component["instructions"] = list(component["instructions"])
        adapted_components.append(new_component)

    def rewrite(lines):
        out = list(lines)
        for old, new in wording_changes:
            out = [line.replace(old, new) for line in out]
        return out

    instructions = rewrite(recipe.get("instructions", []))
    for component in adapted_components:
        if "instructions" in component:
            component["instructions"] = rewrite(component["instructions"])

    final_equipment = sorted(set(equipment))
    for item in final_equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)

    if yield_factor[0] != 1:
        # A substitute that changes yield would move the recipe off the
        # requested target; compensate quantities so the target yield holds.
        compensation = 1 / yield_factor[0]
        warnings.add(
            "quantities rescaled by %s to preserve target yield" % compensation
        )
        for ing in adapted_ingredients:
            ing["quantity"] *= compensation
        for component in adapted_components:
            for ing in component.get("ingredients", []):
                ing["quantity"] *= compensation

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    adapted_recipe = {
        "title": recipe["title"],
        "yield": target_yield,
        "ingredients": adapted_ingredients,
        "equipment": final_equipment,
        "instructions": instructions,
        "components": adapted_components,
        "authored_text": recipe["authored_text"],
    }
    return {
        "possible": True,
        "recipe": adapted_recipe,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
