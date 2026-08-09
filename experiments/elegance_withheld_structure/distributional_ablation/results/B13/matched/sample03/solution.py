from copy import deepcopy
from fractions import Fraction
import re

__all__ = ["adapt", "print_original"]


def print_original(recipe):
    """Return the recipe exactly as it was authored."""
    return recipe["authored_text"]


def _normal(value):
    return str(value).strip().casefold()


def _ingredient_lists(value):
    """Yield every ingredient list in a recipe or its components."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "ingredients" and isinstance(child, list):
                yield child
            elif key != "ingredients":
                yield from _ingredient_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _ingredient_lists(child)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _rewrite_instructions(value, changes):
    """Apply ordered edits only to instruction strings."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "instructions" and isinstance(child, list):
                rewritten = []
                for statement in child:
                    if not isinstance(statement, str):
                        rewritten.append(statement)
                        continue
                    text = statement
                    for kind, old, new in changes:
                        if not old:
                            continue
                        if kind == "ingredient":
                            pattern = r"(?<!\w)" + re.escape(old) + r"(?!\w)"
                            text = re.sub(
                                pattern,
                                lambda _match, replacement=new: replacement,
                                text,
                                flags=re.IGNORECASE,
                            )
                        else:
                            text = text.replace(old, new)
                    rewritten.append(text)
                value[key] = rewritten
            else:
                _rewrite_instructions(child, changes)
    elif isinstance(value, list):
        for child in value:
            _rewrite_instructions(child, changes)


def adapt(recipe, request, catalog):
    """Adapt a recipe without modifying the recipe, request, or catalog."""
    adapted = deepcopy(recipe)
    excluded = {_normal(name) for name in request.get("excluded", [])}
    available = {
        _normal(name) for name in request.get("available_equipment", [])
    }

    ordered_catalog = sorted(
        enumerate(catalog),
        key=lambda entry: (entry[1].get("priority", 0), entry[0]),
    )

    equipment = list(adapted.get("equipment", []))
    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []
    effective_yield = Fraction(adapted["yield"])

    def matching_choice(ingredient):
        name = _normal(ingredient.get("name", ""))
        ingredient_id = _normal(ingredient.get("id", ""))
        for _position, choice in ordered_catalog:
            target = _normal(choice.get("for", ""))
            if target == name or (ingredient_id and target == ingredient_id):
                return choice
        return None

    def remove_equipment(names):
        nonlocal equipment
        removed = {_normal(name) for name in names}
        equipment = [item for item in equipment if _normal(item) not in removed]

    def add_equipment(names):
        present = {_normal(item) for item in equipment}
        for name in names:
            if _normal(name) not in present:
                equipment.append(name)
                present.add(_normal(name))

    def resolve(ingredient, owner, path):
        nonlocal effective_yield

        name = ingredient.get("name", "")
        normalized_name = _normal(name)
        if normalized_name not in excluded:
            return

        if normalized_name in path:
            reasons.add("substitution cycle involving " + str(name))
            return

        choice = matching_choice(ingredient)
        if choice is None:
            reasons.add("no substitution for " + str(name))
            return

        choices.append(choice["id"])
        result = choice.get("result", {})
        next_path = path + (normalized_name,)
        old_name = str(name)
        new_name = str(result.get("name", old_name))

        if new_name != old_name:
            wording_changes.append(("ingredient", old_name, new_name))
        ingredient["name"] = new_name

        if "quantity_factor" in result:
            ingredient["quantity"] = (
                Fraction(ingredient["quantity"])
                * Fraction(result["quantity_factor"])
            )
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        for pair in result.get("wording_changes", []):
            wording_changes.append(
                ("literal", str(pair["old"]), str(pair["new"]))
            )

        removals = []
        additions = []
        for key in ("equipment_removals", "equipment_remove", "remove_equipment"):
            removals.extend(_as_list(result.get(key)))
        for key in ("equipment_additions", "equipment_add", "add_equipment"):
            additions.extend(_as_list(result.get(key)))
        remove_equipment(removals)
        add_equipment(additions)

        if "yield" in result:
            effective_yield = Fraction(result["yield"])
        if "yield_factor" in result:
            effective_yield *= Fraction(result["yield_factor"])

        for source in (choice, result):
            warnings.update(str(item) for item in _as_list(source.get("warnings")))
            warnings.update(str(item) for item in _as_list(source.get("warning")))

        # Finish the replacement chain before processing ingredients it adds.
        resolve(ingredient, owner, next_path)

        for index, additional in enumerate(
            result.get("additional_ingredients", []), start=1
        ):
            introduced = deepcopy(additional)
            introduced.setdefault(
                "id", str(choice["id"]) + ":additional:" + str(index)
            )
            owner.append(introduced)
            resolve(introduced, owner, next_path)

    # Snapshot the authored ingredient locations. Introduced ingredients are
    # resolved immediately by resolve(), so they are not processed twice.
    original_entries = []
    for ingredient_list in _ingredient_lists(adapted):
        original_entries.extend(
            (ingredient, ingredient_list) for ingredient in list(ingredient_list)
        )

    for ingredient, owner in original_entries:
        resolve(ingredient, owner, ())

    add_equipment([])  # Deduplication is already enforced for substitutions.
    adapted["equipment"] = sorted(equipment)

    for item in adapted["equipment"]:
        if _normal(item) not in available:
            reasons.add("equipment " + str(item) + " unavailable")

    if reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": choices,
            "warnings": sorted(warnings),
            "reasons": sorted(reasons),
        }

    target_yield = Fraction(request["target_yield"])
    scale = target_yield / effective_yield
    for ingredient_list in _ingredient_lists(adapted):
        for ingredient in ingredient_list:
            ingredient["quantity"] = Fraction(ingredient["quantity"]) * scale

    adapted["yield"] = target_yield
    _rewrite_instructions(adapted, wording_changes)

    return {
        "possible": True,
        "recipe": adapted,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": [],
    }
