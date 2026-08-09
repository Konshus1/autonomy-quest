from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any, Iterable


def _norm(value: Any) -> str:
    return str(value).strip().casefold()


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    return list(value)


def _fraction(value: Any) -> Fraction:
    return value if isinstance(value, Fraction) else Fraction(value)


def _catalog_choices(catalog: Any) -> list[dict[str, Any]]:
    if isinstance(catalog, dict):
        raw = catalog.get("choices", catalog.get("substitutions", []))
    else:
        raw = catalog
    return list(raw or [])


def _recipe_nodes(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = [recipe]

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return

        is_node = any(
            key in value
            for key in ("ingredients", "equipment", "instructions", "components")
        )
        if is_node:
            nodes.append(value)
            visit(value.get("components", []))
        else:
            for item in value.values():
                visit(item)

    visit(recipe.get("components", []))
    return nodes


def _choice_matches(choice: dict[str, Any], ingredient: dict[str, Any]) -> bool:
    targets = _items(choice.get("for"))
    keys = {_norm(ingredient.get("name", "")), _norm(ingredient.get("id", ""))}
    keys.discard("")
    return any(_norm(target) in keys for target in targets)


def _equipment_values(result: dict[str, Any], addition: bool) -> list[str]:
    keys = (
        ("equipment_additions", "add_equipment", "equipment_add")
        if addition
        else ("equipment_removals", "remove_equipment", "equipment_remove")
    )
    values: list[str] = []
    for key in keys:
        values.extend(str(item) for item in _items(result.get(key)))
    return values


def _change_equipment(
    owner: dict[str, Any], removals: Iterable[str], additions: Iterable[str]
) -> None:
    current = list(owner.get("equipment", []))
    removed = {_norm(item) for item in removals}
    current = [item for item in current if _norm(item) not in removed]

    present = {_norm(item) for item in current}
    for item in additions:
        if _norm(item) not in present:
            current.append(item)
            present.add(_norm(item))
    owner["equipment"] = current


def _collect_warnings(choice: dict[str, Any], result: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for source in (choice, result):
        warnings.extend(str(item) for item in _items(source.get("warnings")))
        if "warning" in source:
            warnings.extend(str(item) for item in _items(source.get("warning")))
    return warnings


def adapt(
    recipe: dict[str, Any], request: dict[str, Any], catalog: Any
) -> dict[str, Any]:
    work = deepcopy(recipe)
    choices: list[Any] = []
    warnings: list[str] = []
    reasons: set[str] = set()
    wording_changes: list[tuple[str, str]] = []

    excluded = {_norm(item) for item in request.get("excluded", [])}
    available = list(request.get("available_equipment", []))
    available_by_key = {_norm(item): item for item in available}

    indexed_catalog = list(enumerate(_catalog_choices(catalog)))

    original_yield = _fraction(work["yield"])
    effective_yield = original_yield

    def candidates(ingredient: dict[str, Any]) -> list[dict[str, Any]]:
        matches = [
            (index, choice)
            for index, choice in indexed_catalog
            if _choice_matches(choice, ingredient)
        ]
        matches.sort(key=lambda pair: (_fraction(pair[1].get("priority", 0)), pair[0]))
        return [choice for _, choice in matches]

    def apply_yield_change(result: dict[str, Any]) -> None:
        nonlocal effective_yield
        if "yield_factor" in result:
            effective_yield *= _fraction(result["yield_factor"])
        elif "yield" in result:
            effective_yield = _fraction(result["yield"])
        elif "yield_change" in result:
            change = result["yield_change"]
            if isinstance(change, dict):
                if "factor" in change:
                    effective_yield *= _fraction(change["factor"])
                elif "yield" in change:
                    effective_yield = _fraction(change["yield"])
            else:
                effective_yield *= _fraction(change)

    def resolve(
        ingredient: dict[str, Any],
        owner: dict[str, Any],
        ancestry: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        current = deepcopy(ingredient)
        name = str(current.get("name", ""))
        key = _norm(name)

        if key not in excluded:
            return [current]

        if key in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            return []

        options = candidates(current)
        if not options:
            reasons.add(f"no substitution for {name}")
            return []

        choice = options[0]
        result = choice.get("result", {})
        choices.append(choice.get("id"))
        warnings.extend(_collect_warnings(choice, result))

        replacement = deepcopy(current)
        replacement["name"] = result.get("name", replacement.get("name"))
        if "id" in result:
            replacement["id"] = result["id"]
        if "quantity_factor" in result:
            replacement["quantity"] = _fraction(replacement["quantity"]) * _fraction(
                result["quantity_factor"]
            )
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        for change in _items(result.get("wording_changes")):
            if isinstance(change, dict) and "old" in change and "new" in change:
                wording_changes.append((str(change["old"]), str(change["new"])))
            elif isinstance(change, (list, tuple)) and len(change) == 2:
                wording_changes.append((str(change[0]), str(change[1])))

        _change_equipment(
            owner,
            _equipment_values(result, addition=False),
            _equipment_values(result, addition=True),
        )
        apply_yield_change(result)

        next_ancestry = ancestry + (key,)
        resolved = resolve(replacement, owner, next_ancestry)
        for additional in _items(result.get("additional_ingredients")):
            resolved.extend(resolve(deepcopy(additional), owner, next_ancestry))
        return resolved

    nodes = _recipe_nodes(work)
    for node in nodes:
        if "ingredients" not in node:
            continue
        owner = node if "equipment" in node else work
        adapted_ingredients: list[dict[str, Any]] = []
        for ingredient in node.get("ingredients", []):
            adapted_ingredients.extend(resolve(ingredient, owner))
        node["ingredients"] = adapted_ingredients

    if effective_yield == 0:
        raise ValueError("effective recipe yield cannot be zero")

    target_yield = _fraction(request["target_yield"])
    scale = target_yield / effective_yield

    for node in nodes:
        for ingredient in node.get("ingredients", []):
            ingredient["quantity"] = _fraction(ingredient["quantity"]) * scale

        if node is not work and "yield" in node:
            node["yield"] = _fraction(node["yield"]) * scale

        if "instructions" in node:
            changed_instructions: list[str] = []
            for instruction in node.get("instructions", []):
                text = instruction
                for old, new in wording_changes:
                    text = text.replace(old, new)
                changed_instructions.append(text)
            node["instructions"] = changed_instructions

    work["yield"] = target_yield

    for node in nodes:
        for ingredient in node.get("ingredients", []):
            name = str(ingredient.get("name", ""))
            if _norm(name) in excluded:
                reasons.add(f"no substitution for {name}")

        if "equipment" in node:
            canonical: list[str] = []
            seen: set[str] = set()
            for item in node.get("equipment", []):
                key = _norm(item)
                if key not in available_by_key:
                    reasons.add(f"equipment {item} unavailable")
                    rendered = str(item)
                else:
                    rendered = available_by_key[key]
                if rendered not in seen:
                    canonical.append(rendered)
                    seen.add(rendered)
            node["equipment"] = sorted(canonical)

    ordered_choices = [choice for choice in choices if choice is not None]
    ordered_warnings = sorted(set(warnings))
    ordered_reasons = sorted(reasons)

    if ordered_reasons:
        return {
            "possible": False,
            "recipe": None,
            "choices": ordered_choices,
            "warnings": ordered_warnings,
            "reasons": ordered_reasons,
        }

    return {
        "possible": True,
        "recipe": work,
        "choices": ordered_choices,
        "warnings": ordered_warnings,
        "reasons": [],
    }


def print_original(recipe: dict[str, Any]) -> str:
    return recipe["authored_text"]
