from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any


DELETE = "__DELETE__"
_MISSING = object()


class _LoadResult:
    def __init__(
        self,
        data: dict[str, Any],
        errors: list[str],
        history: dict[str, list[dict[str, Any]]],
        secrets: set[str],
    ) -> None:
        self.data = data
        self.errors = errors
        self._history = history
        self._secrets = secrets

    def get(self, dotted_key: str, default: Any = None) -> Any:
        current: Any = self.data
        for part in dotted_key.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return default
            current = current[part]
        return current

    def explain(self, dotted_key: str) -> list[dict[str, Any]]:
        masked = dotted_key in self._secrets
        explanation = []
        for candidate in self._history.get(dotted_key, ()):
            value = candidate["value"]
            if masked and value is not None:
                value = "***"
            else:
                value = deepcopy(value)
            explanation.append(
                {
                    "source": candidate["source"],
                    "status": candidate["status"],
                    "value": value,
                }
            )
        return explanation


class _Loader:
    def __init__(self, schema: Mapping[str, str], secrets: tuple[str, ...]) -> None:
        self.schema = dict(schema)
        self.secrets = set(secrets)
        self.data: dict[str, Any] = {}
        self.errors: list[str] = []
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.selected: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _dotted(path: tuple[str, ...]) -> str:
        return ".".join(path)

    def _kind(self, path: tuple[str, ...]) -> tuple[str, str | None]:
        dotted = self._dotted(path)
        if dotted in self.schema:
            return "declared", self.schema[dotted]
        prefix = dotted + "."
        if any(key.startswith(prefix) for key in self.schema):
            return "prefix", None
        return "unknown", None

    def _record_invalid(
        self, path: tuple[str, ...], source: str, value: Any, expected: str
    ) -> None:
        dotted = self._dotted(path)
        self.history.setdefault(dotted, []).append(
            {
                "source": source,
                "status": "invalid",
                "value": deepcopy(value),
            }
        )
        self.errors.append(
            f"invalid {dotted} from {source}: expected {expected}"
        )

    def _record_selected(
        self, path: tuple[str, ...], source: str, value: Any
    ) -> None:
        dotted = self._dotted(path)
        previous = self.selected.pop(dotted, None)
        if previous is not None:
            previous["status"] = "overridden"
        candidate = {
            "source": source,
            "status": "selected",
            "value": deepcopy(value),
        }
        self.history.setdefault(dotted, []).append(candidate)
        self.selected[dotted] = candidate

    def _record_removed(
        self, path: tuple[str, ...], source: str, value: Any
    ) -> None:
        dotted = self._dotted(path)
        self._clear_selected(path)
        self.history.setdefault(dotted, []).append(
            {
                "source": source,
                "status": "removed",
                "value": deepcopy(value),
            }
        )

    def _clear_selected(self, path: tuple[str, ...]) -> None:
        dotted = self._dotted(path)
        prefix = dotted + "."
        for key in list(self.selected):
            if key == dotted or key.startswith(prefix):
                self.selected[key]["status"] = "overridden"
                del self.selected[key]

    def _lookup(self, path: tuple[str, ...]) -> Any:
        current: Any = self.data
        for part in path:
            if not isinstance(current, dict) or part not in current:
                return _MISSING
            current = current[part]
        return current

    def _parent(self, path: tuple[str, ...]) -> dict[str, Any]:
        current = self.data
        walked: list[str] = []
        for part in path[:-1]:
            walked.append(part)
            child = current.get(part, _MISSING)
            if not isinstance(child, dict):
                self._clear_selected(tuple(walked))
                child = {}
                current[part] = child
            current = child
        return current

    def _assign(self, path: tuple[str, ...], value: Any) -> None:
        self._clear_selected(path)
        self._parent(path)[path[-1]] = deepcopy(value)

    def _delete(self, path: tuple[str, ...]) -> None:
        current: Any = self.data
        for part in path[:-1]:
            if not isinstance(current, dict) or part not in current:
                return
            current = current[part]
        if isinstance(current, dict):
            current.pop(path[-1], None)

    def _ensure_mapping(self, path: tuple[str, ...]) -> dict[str, Any]:
        existing = self._lookup(path)
        if isinstance(existing, dict):
            return existing
        self._assign(path, {})
        result = self._lookup(path)
        return result

    @staticmethod
    def _is_delete(value: Any) -> bool:
        return isinstance(value, str) and value == DELETE

    def _merge_untyped(
        self, target: dict[str, Any], incoming: Mapping[Any, Any]
    ) -> None:
        for key, value in incoming.items():
            if self._is_delete(value):
                target.pop(key, None)
            elif isinstance(value, Mapping):
                current = target.get(key, _MISSING)
                if not isinstance(current, dict):
                    current = {}
                    target[key] = current
                self._merge_untyped(current, value)
            else:
                target[key] = deepcopy(value)

    @staticmethod
    def _convert(value: Any, expected: str) -> tuple[bool, Any]:
        try:
            if expected == "bool":
                if isinstance(value, bool):
                    return True, value
                if isinstance(value, str):
                    normalized = value.strip().lower()
                    if normalized in {"true", "yes", "on", "1"}:
                        return True, True
                    if normalized in {"false", "no", "off", "0"}:
                        return True, False
                return False, None

            if expected == "int":
                if isinstance(value, bool):
                    return False, None
                if isinstance(value, int):
                    return True, value
                if isinstance(value, str):
                    return True, int(value, 10)
                return False, None

            if expected == "path":
                if isinstance(value, Path):
                    return True, Path(value)
                if isinstance(value, str):
                    return True, Path(value)
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
                    return True, deepcopy(value)
                return False, None

            if expected == "mapping":
                if isinstance(value, Mapping):
                    return True, deepcopy(dict(value))
                return False, None
        except (TypeError, ValueError, OverflowError):
            return False, None

        return False, None

    def _apply(self, path: tuple[str, ...], value: Any, source: str) -> None:
        kind, expected = self._kind(path)
        dotted = self._dotted(path)

        if kind == "unknown":
            self.errors.append(f"unknown key {dotted}")
            return

        if self._is_delete(value):
            self._delete(path)
            self._record_removed(path, source, value)
            return

        if kind == "declared":
            valid, converted = self._convert(value, expected or "")
            if not valid:
                self._record_invalid(path, source, value, expected or "")
                return

            if expected == "mapping":
                current = self._lookup(path)
                if not isinstance(current, dict):
                    self._assign(path, {})
                    current = self._lookup(path)
                previous = self.selected.pop(dotted, None)
                if previous is not None:
                    previous["status"] = "overridden"
                self._record_selected(path, source, value)
                self._merge_untyped(current, converted)
            else:
                self._assign(path, converted)
                self._record_selected(path, source, value)
            return

        if isinstance(value, Mapping):
            self._ensure_mapping(path)
            for key, child in value.items():
                self._apply(path + (str(key),), child, source)
        else:
            self._assign(path, value)
            self._record_selected(path, source, value)

    def apply_nested(self, values: Mapping[Any, Any], source: str) -> None:
        for key, value in values.items():
            self._apply((str(key),), value, source)

    def apply_environ(self, values: Mapping[str, Any]) -> None:
        for dotted, value in values.items():
            self._apply(tuple(str(dotted).split(".")), value, "environ")


def load(
    defaults: dict[str, Any],
    project: dict[str, Any],
    user: dict[str, Any] | None,
    environ: dict[str, Any],
    explicit: dict[str, Any],
    schema: dict[str, str],
    secrets: tuple[str, ...] = (),
) -> _LoadResult:
    loader = _Loader(schema, secrets)
    loader.apply_nested(defaults, "defaults")
    loader.apply_nested(project, "project")
    if user is not None:
        loader.apply_nested(user, "user")
    loader.apply_environ(environ)
    loader.apply_nested(explicit, "explicit")
    return _LoadResult(
        data=loader.data,
        errors=sorted(loader.errors),
        history=loader.history,
        secrets=loader.secrets,
    )
