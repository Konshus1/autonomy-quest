from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

DELETE = "__DELETE__"

_SOURCES = ("defaults", "project", "user", "environ", "explicit")
_BOOL_TRUE = {"true", "yes", "on", "1"}
_BOOL_FALSE = {"false", "no", "off", "0"}


class Settings:
    def __init__(
        self,
        data: dict[str, Any],
        errors: list[str],
        candidates: dict[str, list[dict[str, Any]]],
        winners: dict[str, int],
        secrets: set[str],
    ) -> None:
        self.data = data
        self.errors = errors
        self._candidates = candidates
        self._winners = winners
        self._secrets = secrets

    def get(self, dotted_key: str, default: Any = None) -> Any:
        current: Any = self.data
        if not dotted_key:
            return current
        for part in dotted_key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def explain(self, dotted_key: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        winner = self._winners.get(dotted_key)
        secret = dotted_key in self._secrets

        for candidate in self._candidates.get(dotted_key, ()):
            state = candidate["state"]
            if state == "invalid":
                status = "invalid"
            elif state == "removed":
                status = "removed"
            elif candidate["id"] == winner:
                status = "selected"
            else:
                status = "overridden"

            value = copy.deepcopy(candidate["value"])
            if secret and value is not None:
                value = "***"
            result.append(
                {
                    "source": candidate["source"],
                    "status": status,
                    "value": value,
                }
            )
        return result


def _convert(value: Any, expected: str) -> tuple[bool, Any]:
    if expected == "bool":
        if isinstance(value, bool):
            return True, value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in _BOOL_TRUE:
                return True, True
            if normalized in _BOOL_FALSE:
                return True, False
        return False, None

    if expected == "int":
        if isinstance(value, bool):
            return False, None
        if isinstance(value, int):
            return True, value
        if isinstance(value, str):
            try:
                return True, int(value, 10)
            except ValueError:
                pass
        return False, None

    if expected == "path":
        if isinstance(value, (str, os.PathLike)):
            try:
                return True, Path(value)
            except (TypeError, ValueError, OSError):
                pass
        return False, None

    if expected == "csv":
        if isinstance(value, str):
            return True, [part.strip() for part in value.split(",")]
        return False, None

    if expected == "str":
        if isinstance(value, str):
            return True, value
        return False, None

    if expected == "list":
        if isinstance(value, list):
            return True, copy.deepcopy(value)
        return False, None

    if expected == "mapping":
        if isinstance(value, dict):
            return True, copy.deepcopy(value)
        return False, None

    return False, None


def _ensure_mapping(data: dict[str, Any], parts: tuple[str, ...]) -> dict[str, Any]:
    current = data
    for part in parts:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    return current


def _set_value(data: dict[str, Any], parts: tuple[str, ...], value: Any) -> None:
    parent = _ensure_mapping(data, parts[:-1])
    parent[parts[-1]] = copy.deepcopy(value)


def _delete_value(data: dict[str, Any], parts: tuple[str, ...]) -> None:
    current: Any = data
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def load(
    defaults: dict[str, Any],
    project: dict[str, Any],
    user: dict[str, Any] | None,
    environ: dict[str, Any],
    explicit: dict[str, Any],
    schema: dict[str, str],
    secrets: tuple[str, ...] | list[str] | set[str] = (),
) -> Settings:
    schema_map = dict(schema)
    prefixes: set[str] = set()
    for dotted in schema_map:
        parts = dotted.split(".")
        prefixes.update(".".join(parts[:index]) for index in range(1, len(parts)))

    data: dict[str, Any] = {}
    errors: set[str] = set()
    candidates: dict[str, list[dict[str, Any]]] = {}
    winners: dict[str, int] = {}
    next_candidate_id = 0

    def record(dotted: str, source: str, state: str, value: Any) -> int:
        nonlocal next_candidate_id
        candidate_id = next_candidate_id
        next_candidate_id += 1
        candidates.setdefault(dotted, []).append(
            {
                "id": candidate_id,
                "source": source,
                "state": state,
                "value": copy.deepcopy(value),
            }
        )
        return candidate_id

    def clear_winners_at_or_below(dotted: str) -> None:
        prefix = dotted + "."
        for key in list(winners):
            if key == dotted or key.startswith(prefix):
                del winners[key]

    def apply(parts: tuple[str, ...], raw: Any, source: str) -> None:
        dotted = ".".join(parts)
        expected = schema_map.get(dotted)
        structural = dotted in prefixes

        if expected is None and not structural:
            errors.add(f"unknown key {dotted}")
            return

        if raw == DELETE:
            candidate_id = record(dotted, source, "removed", raw)
            del candidate_id
            _delete_value(data, parts)
            clear_winners_at_or_below(dotted)
            return

        if expected is not None:
            valid, converted = _convert(raw, expected)
            if not valid:
                record(dotted, source, "invalid", raw)
                errors.add(f"invalid {dotted} from {source}: expected {expected}")
                return

            candidate_id = record(dotted, source, "valid", raw)
            if expected == "mapping":
                _ensure_mapping(data, parts)
                winners[dotted] = candidate_id
                for key, child in raw.items():
                    apply(parts + (str(key),), child, source)
            else:
                _set_value(data, parts, converted)
                clear_winners_at_or_below(dotted)
                winners[dotted] = candidate_id
            return

        if isinstance(raw, dict):
            candidate_id = record(dotted, source, "valid", raw)
            _ensure_mapping(data, parts)
            winners[dotted] = candidate_id
            for key, child in raw.items():
                apply(parts + (str(key),), child, source)
            return

        # Structural prefixes have no declared conversion type. A scalar at
        # such a prefix cannot provide any declared descendant, so its own key
        # is reported as unknown rather than inventing an unsupported type.
        errors.add(f"unknown key {dotted}")

    def apply_nested(mapping: dict[str, Any], source: str) -> None:
        for key, value in mapping.items():
            apply((str(key),), value, source)

    def apply_environment(mapping: dict[str, Any]) -> None:
        for dotted, value in mapping.items():
            key = str(dotted)
            apply(tuple(key.split(".")), value, "environ")

    source_values = {
        "defaults": defaults,
        "project": project,
        "user": user,
        "environ": environ,
        "explicit": explicit,
    }

    for source in _SOURCES:
        mapping = source_values[source]
        if mapping is None:
            continue
        if source == "environ":
            apply_environment(mapping)
        else:
            apply_nested(mapping, source)

    return Settings(
        data=data,
        errors=sorted(errors),
        candidates=candidates,
        winners=winners,
        secrets=set(secrets),
    )
