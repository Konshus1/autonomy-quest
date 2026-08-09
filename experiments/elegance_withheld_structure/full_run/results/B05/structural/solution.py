"""Layered, schema-aware settings resolution."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DELETE = "__DELETE__"
_SOURCES = ("defaults", "project", "user", "environ", "explicit")
_INTEGER = re.compile(r"^[+-]?\d+$")


@dataclass
class _Candidate:
    source: str
    value: Any
    status: str
    order: int


class Settings:
    def __init__(
        self,
        data: dict[str, Any],
        errors: list[str],
        history: dict[str, list[_Candidate]],
        secrets: set[str],
    ) -> None:
        self.data = data
        self.errors = errors
        self._history = history
        self._secrets = secrets

    def get(self, dotted_key: str, default: Any = None) -> Any:
        value: Any = self.data
        if not dotted_key:
            return value
        for part in dotted_key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def explain(self, dotted_key: str) -> list[dict[str, Any]]:
        masked = dotted_key in self._secrets
        result = []
        for candidate in self._history.get(dotted_key, ()):
            value = deepcopy(candidate.value)
            if masked and value is not None:
                value = "***"
            result.append(
                {
                    "source": candidate.source,
                    "status": candidate.status,
                    "value": value,
                }
            )
        return result


def _convert(value: Any, expected: str) -> Any:
    if expected == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "on", "1"}:
                return True
            if normalized in {"false", "no", "off", "0"}:
                return False
        raise ValueError

    if expected == "int":
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            normalized = value.strip()
            if _INTEGER.fullmatch(normalized):
                return int(normalized, 10)
        raise ValueError

    if expected == "path":
        if isinstance(value, Path):
            return deepcopy(value)
        if isinstance(value, str):
            return Path(value)
        raise ValueError

    if expected == "csv":
        if isinstance(value, str):
            return [part.strip() for part in value.split(",")]
        raise ValueError

    if expected == "str":
        if isinstance(value, str):
            return value
        raise ValueError

    if expected == "list":
        if isinstance(value, list):
            return deepcopy(value)
        raise ValueError

    if expected == "mapping":
        if isinstance(value, dict):
            return deepcopy(value)
        raise ValueError

    raise ValueError


def _deep_merge(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def _set_path(
    data: dict[str, Any],
    parts: tuple[str, ...],
    value: Any,
    merge_mapping: bool,
) -> None:
    current = data
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child

    key = parts[-1]
    if (
        merge_mapping
        and isinstance(value, dict)
        and isinstance(current.get(key), dict)
    ):
        _deep_merge(current[key], value)
    else:
        current[key] = deepcopy(value)


def _delete_path(data: dict[str, Any], parts: tuple[str, ...]) -> None:
    current: Any = data
    parents: list[tuple[dict[str, Any], str]] = []

    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        parents.append((current, part))
        current = current[part]

    if isinstance(current, dict):
        current.pop(parts[-1], None)

    for parent, key in reversed(parents):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            del parent[key]
        else:
            break


def load(
    defaults: dict[str, Any],
    project: dict[str, Any],
    user: dict[str, Any] | None,
    environ: dict[str, Any],
    explicit: dict[str, Any],
    schema: dict[str, str],
    secrets=(),
) -> Settings:
    layers = {
        "defaults": deepcopy(defaults),
        "project": deepcopy(project),
        "user": deepcopy(user) if user is not None else {},
        "environ": deepcopy(environ),
        "explicit": deepcopy(explicit),
    }
    declared = dict(schema)
    prefixes = {
        ".".join(parts[:index])
        for dotted in declared
        for parts in (dotted.split("."),)
        for index in range(1, len(parts))
    }

    data: dict[str, Any] = {}
    errors: list[str] = []
    history: dict[str, list[_Candidate]] = {}
    order = 0

    def record(path: str, source: str, value: Any, status: str) -> _Candidate:
        nonlocal order
        candidate = _Candidate(source, deepcopy(value), status, order)
        order += 1
        history.setdefault(path, []).append(candidate)
        return candidate

    def visit(source: str, path: tuple[str, ...], raw: Any) -> None:
        dotted = ".".join(path)
        known = dotted in declared or dotted in prefixes

        if not known:
            errors.append(f"unknown key {dotted}")
            return

        if raw == DELETE:
            record(dotted, source, raw, "removed")
            _delete_path(data, path)
            return

        if dotted in declared:
            expected = declared[dotted]
            try:
                converted = _convert(raw, expected)
            except (TypeError, ValueError):
                record(dotted, source, raw, "invalid")
                errors.append(
                    f"invalid {dotted} from {source}: expected {expected}"
                )
                return

            for earlier in history.get(dotted, ()):
                if earlier.status == "selected":
                    earlier.status = "overridden"

            record(dotted, source, raw, "selected")
            _set_path(data, path, converted, expected == "mapping")
            return

        if not isinstance(raw, dict):
            record(dotted, source, raw, "invalid")
            errors.append(f"invalid {dotted} from {source}: expected mapping")
            return

        for key, value in raw.items():
            visit(source, path + (str(key),), value)

    for source in _SOURCES:
        layer = layers[source]
        if source == "environ":
            for dotted, value in layer.items():
                visit(source, tuple(str(dotted).split(".")), value)
        else:
            for key, value in layer.items():
                visit(source, (str(key),), value)

    removals = [
        (candidate.order, dotted)
        for dotted, candidates in history.items()
        for candidate in candidates
        if candidate.status == "removed"
    ]

    for dotted, candidates in history.items():
        for candidate in candidates:
            if candidate.status != "selected":
                continue
            if any(
                removal_order > candidate.order
                and (
                    dotted == removed_path
                    or dotted.startswith(removed_path + ".")
                )
                for removal_order, removed_path in removals
            ):
                candidate.status = "overridden"

    return Settings(data, sorted(errors), history, set(secrets))
