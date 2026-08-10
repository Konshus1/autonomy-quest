"""recipebook: adapt recipes to a target yield, available equipment, and
dietary exclusions using a substitution catalog. Standard library only."""

from fractions import Fraction
import copy


def print_original(recipe):
    """Return the recipe exactly as authored, byte-for-byte."""
    return recipe["authored_text"]


def _best_choice(name, catalog):
    """Lowest numeric priority wins; catalog order breaks ties."""
    best = None
    best_key = None
    for index, choice in enumerate(catalog):
        if choice["for"] != name:
            continue
        key = (choice["priority"], index)
        if best_key is None or key < best_key:
            best_key = key
            best = choice
    return best


def adapt(recipe, request, catalog):
    scale = Fraction(request["target_yield"]) / Fraction(recipe["yield"])
    available = set(request["available_equipment"])
    excluded = set(request["excluded"])

    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    equipment = list(recipe["equipment"])
    final_yield = Fraction(request["target_yield"])

    def scaled_copy(raw):
        return {
            "id": raw.get("id", raw["name"]),
            "name": raw["name"],
            "quantity": Fraction(raw["quantity"]) * scale,
            "unit": raw.get("unit"),
            "preparation": raw.get("preparation"),
        }

    def apply_result(ing, choice):
        nonlocal final_yield
        result = choice["result"]
        if "quantity_factor" in result:
            ing["quantity"] = ing["quantity"] * Fraction(result["quantity_factor"])
        if "unit" in result and result["unit"] != ing["unit"]:
            warnings.add("unit changed for %s: %s -> %s"
                         % (result["name"], ing["unit"], result["unit"]))
            ing["unit"] = result["unit"]
        if "preparation" in result:
            ing["preparation"] = result["preparation"]
        for pair in result.get("wording_changes", []):
            wording_changes.append((pair["old"], pair["new"]))
        for item in result.get("equipment_add", result.get("add_equipment", [])):
            if item not in equipment:
                equipment.append(item)
                warnings.add("equipment added: %s" % item)
        for item in result.get("equipment_remove", result.get("remove_equipment", [])):
            if item in equipment:
                equipment[:] = [e for e in equipment if e != item]
                warnings.add("equipment removed: %s" % item)
        if "yield_factor" in result:
            final_yield *= Fraction(result["yield_factor"])
            warnings.add("yield changed by %s" % choice["id"])
        ing["name"] = result["name"]
        return result.get("additional_ingredients", result.get("additional", []))

    def resolve(ing):
        """Substitute until the ingredient is allowed; return introduced extras."""
        seen = {ing["name"]}
        extras = []
        while ing["name"] in excluded:
            choice = _best_choice(ing["name"], catalog)
            if choice is None:
                reasons.add("no substitution for %s" % ing["name"])
                break
            choices.append(choice["id"])
            replacement = choice["result"]["name"]
            if replacement in seen:
                reasons.add("substitution cycle involving %s" % replacement)
                break
            extras.extend(apply_result(ing, choice))
            seen.add(replacement)
        return extras

    def process_list(raw_list):
        out = []
        for raw in raw_list:
            queue = [scaled_copy(raw)]
            while queue:
                ing = queue.pop(0)
                for extra in resolve(ing):
                    queue.append(scaled_copy(extra))
                out.append(ing)
        return out

    ingredients = process_list(recipe["ingredients"])

    components = []
    for comp in recipe.get("components", []):
        if isinstance(comp, dict) and "ingredients" in comp:
            adapted_comp = {key: copy.deepcopy(value)
                            for key, value in comp.items() if key != "ingredients"}
            adapted_comp["ingredients"] = process_list(comp["ingredients"])
            components.append(adapted_comp)
        else:
            components.append(copy.deepcopy(comp))

    instructions = list(recipe["instructions"])
    for old, new in wording_changes:
        instructions = [line.replace(old, new) for line in instructions]

    final_equipment = sorted(set(equipment))
    for item in final_equipment:
        if item not in available:
            reasons.add("equipment %s unavailable" % item)

    if reasons:
        return {"possible": False, "recipe": None, "choices": choices,
                "warnings": sorted(warnings), "reasons": sorted(reasons)}

    adapted = {
        "title": recipe["title"],
        "yield": final_yield,
        "ingredients": ingredients,
        "equipment": final_equipment,
        "instructions": instructions,
        "components": components,
        "authored_text": recipe["authored_text"],
    }
    return {"possible": True, "recipe": adapted, "choices": choices,
            "warnings": sorted(warnings), "reasons": []}
