from collections import deque
from copy import deepcopy
from fractions import Fraction


def _norm(value):
    return str(value).casefold()


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _fraction(value):
    return value if isinstance(value, Fraction) else Fraction(value)


def _recipe_nodes(recipe):
    """Return the recipe and every dictionary nested beneath components."""
    nodes = [recipe]
    seen = {id(recipe)}

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            marker = id(value)
            if marker in seen:
                return
            seen.add(marker)
            nodes.append(value)
            if "components" in value:
                visit(value["components"])
            # A components object may be a mapping of component names to records.
            if not any(
                key in value
                for key in ("ingredients", "instructions", "equipment", "components")
            ):
                for item in value.values():
                    visit(item)

    visit(recipe.get("components", []))
    return nodes


def _choice_for(ingredient, catalog):
    names = {_norm(ingredient.get("name", ""))}
    if ingredient.get("id") is not None:
        names.add(_norm(ingredient["id"]))

    candidates = []
    for index, choice in enumerate(catalog):
        targets = {_norm(item) for item in _as_list(choice.get("for"))}
        if names & targets:
            candidates.append((choice.get("priority", 0), index, choice))

    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _wording_pairs(result):
    changes = result.get("wording_changes", [])
    if isinstance(changes, dict) and "old" not in changes:
        changes = [{"old": old, "new": new} for old, new in changes.items()]

    pairs = []
    for change in _as_list(changes):
        if isinstance(change, dict):
            if "old" in change and "new" in change:
                pairs.append((str(change["old"]), str(change["new"])))
        elif isinstance(change, (list, tuple)) and len(change) == 2:
            pairs.append((str(change[0]), str(change[1])))
    return pairs


def _equipment_changes(result):
    additions = []
    removals = []

    for key in ("equipment_additions", "add_equipment", "equipment_added"):
        additions.extend(_as_list(result.get(key)))
    for key in ("equipment_removals", "remove_equipment", "equipment_removed"):
        removals.extend(_as_list(result.get(key)))

    nested = result.get("equipment_changes")
    if nested is None and isinstance(result.get("equipment"), dict):
        nested = result["equipment"]
    if isinstance(nested, dict):
        additions.extend(_as_list(nested.get("add")))
        additions.extend(_as_list(nested.get("additions")))
        removals.extend(_as_list(nested.get("remove")))
        removals.extend(_as_list(nested.get("removals")))

    return additions, removals


def _remove_equipment(nodes, removals):
    removed = {_norm(item) for item in removals}
    if not removed:
        return
    for node in nodes:
        equipment = node.get("equipment")
        if isinstance(equipment, list):
            node["equipment"] = [
                item for item in equipment if _norm(item) not in removed
            ]


def _add_equipment(recipe, additions):
    equipment = recipe.setdefault("equipment", [])
    present = {_norm(item) for item in equipment}
    for item in additions:
        if _norm(item) not in present:
            equipment.append(item)
            present.add(_norm(item))


def _additional_ingredients(result):
    additional = result.get("additional_ingredients")
    if additional is None:
        additional = result.get("additional", [])
    return _as_list(additional)


def print_original(recipe):
    return recipe["authored_text"]


def adapt(recipe, request, catalog):
    adapted = deepcopy(recipe)
    nodes = _recipe_nodes(adapted)

    excluded = {_norm(item) for item in request.get("excluded", [])}
    available = {_norm(item) for item in request.get("available_equipment", [])}
    target_yield = _fraction(request["target_yield"])
    effective_yield = _fraction(adapted["yield"])

    choices = []
    warnings = set()
    reasons = set()
    wording_changes = []

    # Each queue entry carries its containing list so additions can remain with
    # the ingredient/component that caused them.
    queue = deque()
    for node in nodes:
        ingredients = node.get("ingredients")
        if isinstance(ingredients, list):
            for ingredient in ingredients:
                if isinstance(ingredient, dict):
                    queue.append((ingredient, ingredients, ()))

    while queue:
        ingredient, container, ancestry = queue.popleft()
        name = ingredient.get("name", "")
        normalized_name = _norm(name)

        if normalized_name not in excluded:
            continue

        if normalized_name in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            continue

        choice = _choice_for(ingredient, catalog)
        if choice is None:
            reasons.add(f"no substitution for {name}")
            continue

        choice_id = choice["id"]
        choices.append(choice_id)
        result = choice.get("result", {})

        for warning in _as_list(choice.get("warnings")):
            warnings.add(str(warning))
        for warning in _as_list(choice.get("warning")):
            warnings.add(str(warning))
        for warning in _as_list(result.get("warnings")):
            warnings.add(str(warning))
        for warning in _as_list(result.get("warning")):
            warnings.add(str(warning))

        wording_changes.extend(_wording_pairs(result))
        additions, removals = _equipment_changes(result)
        _remove_equipment(nodes, removals)
        _add_equipment(adapted, additions)

        if "yield" in result:
            effective_yield = _fraction(result["yield"])
        if "yield_factor" in result:
            effective_yield *= _fraction(result["yield_factor"])

        if "quantity_factor" in result:
            ingredient["quantity"] = (
                _fraction(ingredient["quantity"])
                * _fraction(result["quantity_factor"])
            )
        if "name" in result:
            ingredient["name"] = result["name"]
        if "unit" in result:
            ingredient["unit"] = result["unit"]
        if "preparation" in result:
            ingredient["preparation"] = result["preparation"]

        next_ancestry = ancestry + (normalized_name,)
        queue.append((ingredient, container, next_ancestry))

        for index, extra_source in enumerate(_additional_ingredients(result)):
            extra = deepcopy(extra_source)
            if not isinstance(extra, dict):
                continue
            extra.setdefault(
                "id",
                f"{ingredient.get('id', 'ingredient')}::{choice_id}::{index}",
            )
            extra.setdefault("preparation", "")
            container.append(extra)
            queue.append((extra, container, next_ancestry))

    # Apply selected textual edits in selection order, allowing intentional
    # chained wording transformations.
    for node in nodes:
        instructions = node.get("instructions")
        if not isinstance(instructions, list):
            continue
        rewritten = []
        for instruction in instructions:
            text = instruction
            if isinstance(text, str):
                for old, new in wording_changes:
                    text = text.replace(old, new)
            rewritten.append(text)
        node["instructions"] = rewritten

    # A substitution's yield change alters the unscaled batch yield. Scale only
    # after all substitutions so every original and introduced quantity receives
    # the same exact rational target-yield factor.
    scale = target_yield / effective_yield
    for node in nodes:
        ingredients = node.get("ingredients")
        if isinstance(ingredients, list):
            for ingredient in ingredients:
                if isinstance(ingredient, dict) and "quantity" in ingredient:
                    ingredient["quantity"] = _fraction(ingredient["quantity"]) * scale

    adapted["yield"] = target_yield

    # Sort every equipment collection and validate all component requirements.
    for node in nodes:
        equipment = node.get("equipment")
        if not isinstance(equipment, list):
            continue
        node["equipment"] = sorted(set(equipment))
        for item in node["equipment"]:
            if _norm(item) not in available:
                reasons.add(f"equipment {item} unavailable")

    ordered_reasons = sorted(reasons)
    return {
        "possible": not ordered_reasons,
        "recipe": adapted if not ordered_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": ordered_reasons,
    }
