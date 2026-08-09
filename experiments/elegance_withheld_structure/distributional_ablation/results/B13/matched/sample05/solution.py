"""Recipe adaptation with exact quantities and deterministic substitutions."""

from copy import deepcopy
from fractions import Fraction


def print_original(recipe):
    """Return the recipe's authored text byte-for-byte."""
    return recipe["authored_text"]


def _key(value):
    return str(value).strip().casefold()


def _containers(root):
    yield root
    components = root.get("components", [])
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                yield from _containers(component)


def _ingredient_locations(root):
    for container in _containers(root):
        ingredients = container.get("ingredients")
        if isinstance(ingredients, list):
            for ingredient in ingredients:
                if isinstance(ingredient, dict):
                    yield ingredient, ingredients


def _apply_wording(root, old, new):
    for container in _containers(root):
        instructions = container.get("instructions")
        if isinstance(instructions, list):
            container["instructions"] = [
                text.replace(old, new) if isinstance(text, str) else text
                for text in instructions
            ]


def _update_equipment(recipe, result):
    equipment = list(recipe.get("equipment", []))

    removals = list(result.get("equipment_remove", []))
    removals.extend(result.get("equipment_removals", []))
    additions = list(result.get("equipment_add", []))
    additions.extend(result.get("equipment_additions", []))

    nested = result.get("equipment")
    if isinstance(nested, dict):
        removals.extend(nested.get("remove", []))
        additions.extend(nested.get("add", []))

    removed = {_key(item) for item in removals}
    equipment = [item for item in equipment if _key(item) not in removed]

    present = {_key(item) for item in equipment}
    for item in additions:
        if _key(item) not in present:
            equipment.append(item)
            present.add(_key(item))

    recipe["equipment"] = equipment


def _catalog_index(catalog):
    indexed = {}
    for position, choice in enumerate(catalog):
        indexed.setdefault(_key(choice["for"]), []).append(
            (choice["priority"], position, choice)
        )
    for alternatives in indexed.values():
        alternatives.sort(key=lambda entry: (entry[0], entry[1]))
    return indexed


def adapt(recipe, request, catalog):
    """Return a complete adapted recipe or all applicable failure reasons."""
    adapted = deepcopy(recipe)
    excluded = {_key(name) for name in request.get("excluded", [])}
    available = {
        _key(name) for name in request.get("available_equipment", [])
    }
    alternatives = _catalog_index(catalog)

    choices = []
    warnings = []
    reasons = set()
    effective_yield = Fraction(adapted["yield"])

    # Queue entries retain ancestry for this particular ingredient, allowing
    # independent occurrences of the same ingredient without false cycles.
    queue = []
    for ingredient, owner in list(_ingredient_locations(adapted)):
        if _key(ingredient.get("name", "")) in excluded:
            queue.append((ingredient, owner, ()))

    cursor = 0
    while cursor < len(queue):
        ingredient, owner, ancestry = queue[cursor]
        cursor += 1

        name = ingredient.get("name", "")
        normalized = _key(name)
        if normalized not in excluded:
            continue

        if normalized in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            continue

        candidates = alternatives.get(normalized, [])
        if not candidates:
            reasons.add(f"no substitution for {name}")
            continue

        choice = candidates[0][2]
        result = choice["result"]
        choices.append(choice["id"])

        ingredient["name"] = result["name"]
        ingredient["quantity"] = (
            Fraction(ingredient["quantity"])
            * Fraction(result.get("quantity_factor", 1))
        )
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        for change in result.get("wording_changes", []):
            _apply_wording(adapted, change["old"], change["new"])

        _update_equipment(adapted, result)

        # An absolute substitution yield replaces the effective authored yield;
        # a yield factor changes it proportionally. Final output is still scaled
        # to request.target_yield.
        if "yield" in result:
            effective_yield = Fraction(result["yield"])
        if "yield_factor" in result:
            effective_yield *= Fraction(result["yield_factor"])

        next_ancestry = ancestry + (normalized,)
        if _key(ingredient.get("name", "")) in excluded:
            queue.append((ingredient, owner, next_ancestry))

        additions = result.get("additional_ingredients", [])
        if isinstance(additions, list):
            for number, supplied in enumerate(additions, 1):
                addition = deepcopy(supplied)
                addition.setdefault(
                    "id", f"{choice['id']}:additional:{number}"
                )
                addition.setdefault("quantity", Fraction(0))
                addition.setdefault("unit", "")
                addition.setdefault("preparation", "")
                addition["quantity"] = Fraction(addition["quantity"])
                owner.append(addition)

                if _key(addition.get("name", "")) in excluded:
                    queue.append((addition, owner, next_ancestry))

    target_yield = Fraction(request["target_yield"])
    scale = target_yield / effective_yield
    for ingredient, _ in _ingredient_locations(adapted):
        ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale
    adapted["yield"] = target_yield

    # Validate every component so failures are collected rather than stopping
    # at the first missing item.
    for container in _containers(adapted):
        equipment = container.get("equipment")
        if isinstance(equipment, list):
            unique = {}
            for item in equipment:
                unique.setdefault(_key(item), item)
            container["equipment"] = sorted(unique.values())
            for item in container["equipment"]:
                if _key(item) not in available:
                    reasons.add(f"equipment {item} unavailable")

    # Defense-in-depth validation catches exclusions introduced by additions or
    # unusual shared structures.
    for ingredient, _ in _ingredient_locations(adapted):
        name = ingredient.get("name", "")
        if _key(name) not in excluded:
            continue
        exact_no_substitution = f"no substitution for {name}"
        exact_cycle = f"substitution cycle involving {name}"
        if (
            exact_no_substitution not in reasons
            and exact_cycle not in reasons
        ):
            reasons.add(exact_no_substitution)

    ordered_reasons = sorted(reasons)
    if ordered_reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": ordered_reasons,
        }

    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
