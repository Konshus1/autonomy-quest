from __future__ import annotations

import copy
import re
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence


def _normalized(value: Any) -> str:
    return str(value).strip().casefold()


def _component_sections(section: Mapping[str, Any]) -> Iterable[MutableMapping[str, Any]]:
    components = section.get("components", [])
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                yield component
    elif isinstance(components, dict):
        for component in components.values():
            if isinstance(component, dict):
                yield component


def _all_sections(recipe: MutableMapping[str, Any]) -> Iterable[MutableMapping[str, Any]]:
    yield recipe
    for component in _component_sections(recipe):
        yield from _all_sections(component)


def _equipment_values(result: Mapping[str, Any], action: str) -> List[str]:
    if action == "add":
        keys = ("equipment_additions", "equipment_add", "add_equipment")
    else:
        keys = ("equipment_removals", "equipment_remove", "remove_equipment")

    values: List[str] = []
    for key in keys:
        candidate = result.get(key, [])
        if isinstance(candidate, str):
            values.append(candidate)
        elif candidate:
            values.extend(candidate)

    equipment = result.get("equipment")
    if isinstance(equipment, Mapping):
        candidate = equipment.get(action, [])
        if isinstance(candidate, str):
            values.append(candidate)
        elif candidate:
            values.extend(candidate)
    return values


def _replace_name(text: str, old: str, new: str) -> str:
    if not old or old == new:
        return text

    pattern = re.compile(
        r"(?<!\w)" + re.escape(old) + r"(?!\w)",
        flags=re.IGNORECASE,
    )

    def replacement(match: re.Match[str]) -> str:
        found = match.group(0)
        if found.isupper():
            return new.upper()
        if found[:1].isupper():
            return new[:1].upper() + new[1:]
        return new

    return pattern.sub(replacement, text)


def adapt(
    recipe: Mapping[str, Any],
    request: Mapping[str, Any],
    catalog: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    adapted = copy.deepcopy(recipe)
    target_yield = request["target_yield"]
    original_yield = recipe["yield"]
    scale = target_yield / original_yield
    adapted["yield"] = target_yield

    excluded = {_normalized(name) for name in request.get("excluded", [])}
    available_equipment = set(request.get("available_equipment", []))

    catalog_by_name: Dict[str, List[tuple[Any, int, Mapping[str, Any]]]] = {}
    for index, choice in enumerate(catalog):
        catalog_by_name.setdefault(_normalized(choice["for"]), []).append(
            (choice["priority"], index, choice)
        )
    for candidates in catalog_by_name.values():
        candidates.sort(key=lambda item: (item[0], item[1]))

    choices: List[str] = []
    warnings: List[str] = []
    reasons: set[str] = set()
    text_changes: List[tuple[str, str, bool]] = []
    equipment_changes: List[tuple[str, str]] = []

    for section in _all_sections(adapted):
        for ingredient in section.get("ingredients", []):
            ingredient["quantity"] *= scale

    def resolve_ingredient(
        ingredient: MutableMapping[str, Any],
        ancestry: tuple[str, ...] = (),
    ) -> List[MutableMapping[str, Any]]:
        name = str(ingredient["name"])
        normalized_name = _normalized(name)

        if normalized_name not in excluded:
            return [ingredient]

        if normalized_name in ancestry:
            reasons.add(f"substitution cycle involving {name}")
            return [ingredient]

        candidates = catalog_by_name.get(normalized_name, [])
        if not candidates:
            reasons.add(f"no substitution for {name}")
            return [ingredient]

        choice = candidates[0][2]
        choices.append(str(choice["id"]))
        result = choice["result"]

        replacement = copy.deepcopy(ingredient)
        replacement_name = str(result["name"])
        replacement["name"] = replacement_name
        replacement["quantity"] *= result.get("quantity_factor", Fraction(1))
        if "unit" in result:
            replacement["unit"] = result["unit"]
        if "preparation" in result:
            replacement["preparation"] = result["preparation"]

        if name != replacement_name:
            text_changes.append((name, replacement_name, True))

        for wording_change in result.get("wording_changes", []):
            text_changes.append(
                (str(wording_change["old"]), str(wording_change["new"]), False)
            )

        for equipment in _equipment_values(result, "remove"):
            equipment_changes.append(("remove", equipment))
        for equipment in _equipment_values(result, "add"):
            equipment_changes.append(("add", equipment))

        next_ancestry = ancestry + (normalized_name,)
        resolved = resolve_ingredient(replacement, next_ancestry)

        for additional in result.get("additional_ingredients", []):
            added = copy.deepcopy(additional)
            added["quantity"] *= scale
            resolved.extend(resolve_ingredient(added, next_ancestry))

        return resolved

    def resolve_section(section: MutableMapping[str, Any]) -> None:
        resolved_ingredients: List[MutableMapping[str, Any]] = []
        for ingredient in section.get("ingredients", []):
            resolved_ingredients.extend(resolve_ingredient(ingredient))
        section["ingredients"] = resolved_ingredients
        for component in _component_sections(section):
            resolve_section(component)

    resolve_section(adapted)

    sections = list(_all_sections(adapted))
    for action, equipment in equipment_changes:
        if action == "remove":
            for section in sections:
                section["equipment"] = [
                    item for item in section.get("equipment", []) if item != equipment
                ]
        elif equipment not in adapted.setdefault("equipment", []):
            adapted["equipment"].append(equipment)

    for section in sections:
        section["equipment"] = sorted(set(section.get("equipment", [])))
        rewritten: List[str] = []
        for instruction in section.get("instructions", []):
            text = instruction
            for old, new, is_name_change in text_changes:
                if is_name_change:
                    text = _replace_name(text, old, new)
                else:
                    text = text.replace(old, new)
            rewritten.append(text)
        section["instructions"] = rewritten

        for equipment in section["equipment"]:
            if equipment not in available_equipment:
                reasons.add(f"equipment {equipment} unavailable")

        for ingredient in section.get("ingredients", []):
            name = str(ingredient["name"])
            if _normalized(name) in excluded:
                normalized_name = _normalized(name)
                if not any(
                    reason == f"no substitution for {name}"
                    or reason == f"substitution cycle involving {name}"
                    for reason in reasons
                ):
                    if normalized_name in catalog_by_name:
                        reasons.add(f"substitution cycle involving {name}")
                    else:
                        reasons.add(f"no substitution for {name}")

    sorted_reasons = sorted(reasons)
    return {
        "possible": not sorted_reasons,
        "recipe": adapted if not sorted_reasons else None,
        "choices": choices,
        "warnings": sorted(warnings),
        "reasons": sorted_reasons,
    }


def print_original(recipe: Mapping[str, Any]) -> str:
    return recipe["authored_text"]
