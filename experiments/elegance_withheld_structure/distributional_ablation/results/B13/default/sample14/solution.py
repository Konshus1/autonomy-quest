from copy import deepcopy
from fractions import Fraction
from math import inf


_PATH = "__recipebook_path"
_USED = "__recipebook_used"
_ALLOW_ID = "__recipebook_allow_id"
_BLOCKED = "__recipebook_blocked"
_CHOICES = "__recipebook_choices"
_WORDING = "__recipebook_wording"
_REASONS = "__recipebook_reasons"


def _key(value):
    return str(value).casefold()


def _component_sections(value):
    if isinstance(value, list):
        for item in value:
            yield from _component_sections(item)
    elif isinstance(value, dict):
        is_section = any(
            key in value for key in ("ingredients", "equipment", "instructions")
        )
        if is_section:
            yield value
            yield from _component_sections(value.get("components", []))
        else:
            for item in value.values():
                yield from _component_sections(item)


def _sections(recipe):
    yield recipe
    yield from _component_sections(recipe.get("components", []))


def _catalog_entries(catalog):
    if isinstance(catalog, dict):
        raw = catalog.get("choices", catalog.get("substitutions", []))
    else:
        raw = catalog

    return sorted(
        enumerate(raw or []),
        key=lambda pair: (pair[1].get("priority", inf), pair[0]),
    )


def _targets(choice):
    target = choice.get("for")
    if isinstance(target, (list, tuple, set)):
        return {_key(item) for item in target}
    return {_key(target)}


def _matching_choices(ingredient, entries):
    name = _key(ingredient.get("name", ""))
    identifier = _key(ingredient.get("id", ""))
    allow_identifier = ingredient.get(_ALLOW_ID, True)

    matches = []
    for catalog_index, choice in entries:
        targets = _targets(choice)
        if name in targets or (allow_identifier and identifier in targets):
            matches.append((catalog_index, choice))
    return matches


def _equipment_modifications(result):
    nested = result.get("equipment", {})
    if not isinstance(nested, dict):
        nested = {}

    additions = result.get(
        "equipment_additions",
        result.get("add_equipment", nested.get("add", [])),
    )
    removals = result.get(
        "equipment_removals",
        result.get("remove_equipment", nested.get("remove", [])),
    )
    return list(additions or []), list(removals or [])


def _change_equipment(section, additions, removals):
    removed = {_key(item) for item in removals}
    equipment = [
        item
        for item in section.get("equipment", [])
        if _key(item) not in removed
    ]

    known = {_key(item) for item in equipment}
    for item in additions:
        normalized = _key(item)
        if normalized not in known:
            equipment.append(item)
            known.add(normalized)

    section["equipment"] = equipment


def _equipment_reasons(recipe, available):
    reasons = set()
    for section in _sections(recipe):
        for item in section.get("equipment", []):
            if _key(item) not in available:
                reasons.add(f"equipment {item} unavailable")
    return reasons


def _strip_metadata(recipe):
    for section in _sections(recipe):
        for ingredient in section.get("ingredients", []):
            ingredient.pop(_PATH, None)
            ingredient.pop(_USED, None)
            ingredient.pop(_ALLOW_ID, None)
            ingredient.pop(_BLOCKED, None)

    recipe.pop(_CHOICES, None)
    recipe.pop(_WORDING, None)
    recipe.pop(_REASONS, None)


def adapt(recipe, request, catalog):
    work = deepcopy(recipe)
    target_yield = request["target_yield"]
    scale = target_yield / work["yield"]
    excluded = {_key(item) for item in request.get("excluded", [])}
    entries = _catalog_entries(catalog)

    available_items = list(request.get("available_equipment", []))
    available = {}
    for item in available_items:
        available.setdefault(_key(item), item)

    for section in _sections(work):
        for ingredient in section.get("ingredients", []):
            ingredient["quantity"] *= scale
            ingredient[_PATH] = []
            ingredient[_USED] = []
            ingredient[_ALLOW_ID] = True
            ingredient[_BLOCKED] = False

    work["yield"] = target_yield
    work[_CHOICES] = []
    work[_WORDING] = []
    work[_REASONS] = set()

    def first_excluded(state):
        for section in _sections(state):
            for index, ingredient in enumerate(section.get("ingredients", [])):
                if ingredient.get(_BLOCKED, False):
                    continue
                if _key(ingredient.get("name", "")) in excluded:
                    return section, index, ingredient
        return None

    def finish(state):
        equipment_reasons = _equipment_reasons(state, available)
        accumulated = set(state[_REASONS]) | equipment_reasons
        if accumulated:
            return None, accumulated, list(state[_CHOICES])

        for old, new in state[_WORDING]:
            for section in _sections(state):
                section["instructions"] = [
                    instruction.replace(old, new)
                    for instruction in section.get("instructions", [])
                ]

        for section in _sections(state):
            normalized_equipment = {
                available[_key(item)] for item in section.get("equipment", [])
            }
            section["equipment"] = sorted(normalized_equipment)

        choices = list(state[_CHOICES])
        _strip_metadata(state)
        return state, set(), choices

    def search(state):
        found = first_excluded(state)
        if found is None:
            return finish(state)

        _, _, current = found
        matches = _matching_choices(current, entries)
        used = set(current[_USED])
        candidates = [
            (catalog_index, choice)
            for catalog_index, choice in matches
            if catalog_index not in used
        ]

        if not candidates:
            branch = deepcopy(state)
            _, _, blocked = first_excluded(branch)
            blocked[_BLOCKED] = True

            if matches:
                reason = f"substitution cycle involving {blocked['name']}"
            else:
                reason = f"no substitution for {blocked['name']}"
            branch[_REASONS].add(reason)
            return search(branch)

        existing_reasons = bool(state[_REASONS])
        all_reasons = set()
        considered = []

        for catalog_index, choice in candidates:
            branch = deepcopy(state)
            section, index, ingredient = first_excluded(branch)
            result = choice["result"]

            path = ingredient[_PATH] + [_key(ingredient["name"])]
            chain_used = ingredient[_USED] + [catalog_index]

            ingredient["name"] = result["name"]
            ingredient["quantity"] *= result.get(
                "quantity_factor", Fraction(1, 1)
            )

            for field in ("unit", "preparation"):
                if field in result:
                    ingredient[field] = result[field]

            if "id" in result:
                ingredient["id"] = result["id"]

            ingredient[_PATH] = path
            ingredient[_USED] = chain_used
            ingredient[_ALLOW_ID] = "id" in result
            ingredient[_BLOCKED] = False

            additions, removals = _equipment_modifications(result)
            _change_equipment(section, additions, removals)

            extras = deepcopy(
                result.get(
                    "additional_ingredients",
                    result.get("add_ingredients", []),
                )
                or []
            )
            for extra in extras:
                extra["quantity"] *= scale
                extra[_PATH] = list(path)
                extra[_USED] = list(chain_used)
                extra[_ALLOW_ID] = True
                extra[_BLOCKED] = False

            ingredients = section.setdefault("ingredients", [])
            ingredients[index + 1:index + 1] = extras

            for change in result.get("wording_changes", []) or []:
                branch[_WORDING].append((change["old"], change["new"]))

            branch[_CHOICES].append(choice["id"])
            completed, reasons, branch_choices = search(branch)

            if completed is not None:
                return completed, set(), branch_choices

            all_reasons.update(reasons)
            for choice_id in branch_choices:
                if choice_id not in considered:
                    considered.append(choice_id)

            # Once an independent failure is already known, continuing with the
            # highest-priority choice is deterministic and lets the search collect
            # failures from the remaining ingredients.
            if existing_reasons:
                return None, all_reasons, considered

        return None, all_reasons, considered

    completed, reasons, choices = search(work)
    return {
        "possible": completed is not None,
        "recipe": completed,
        "choices": choices,
        "warnings": [],
        "reasons": sorted(reasons),
    }


def print_original(recipe):
    return recipe["authored_text"]
