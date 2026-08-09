from copy import deepcopy
from pathlib import Path

DELETE = "__DELETE__"
_MISSING = object()


class Settings:
    def __init__(self, data, errors, provenance, secrets):
        self.data = data
        self.errors = errors
        self._provenance = provenance
        self._secrets = frozenset(secrets)

    def get(self, dotted_key, default=None):
        value = _lookup(self.data, dotted_key)
        return default if value is _MISSING else value

    def explain(self, dotted_key):
        entries = self._provenance.get(dotted_key)
        if not entries:
            return []

        winner = None
        if _lookup(self.data, dotted_key) is not _MISSING:
            for index in range(len(entries) - 1, -1, -1):
                if entries[index]["kind"] == "valid":
                    winner = index
                    break

        secret = dotted_key in self._secrets
        explanation = []

        for index, entry in enumerate(entries):
            kind = entry["kind"]
            if kind == "invalid":
                status = "invalid"
            elif kind == "removed":
                status = "removed"
            elif index == winner:
                status = "selected"
            else:
                status = "overridden"

            value = deepcopy(entry["value"])
            if secret and value is not None:
                value = "***"

            explanation.append(
                {
                    "source": entry["source"],
                    "status": status,
                    "value": value,
                }
            )

        return explanation


def _parts(dotted_key):
    return dotted_key.split(".") if dotted_key else []


def _lookup(root, dotted_key):
    current = root
    for part in _parts(dotted_key):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _assign(root, dotted_key, value):
    parts = _parts(dotted_key)
    if not parts:
        return

    current = root
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child

    current[parts[-1]] = deepcopy(value)


def _remove(root, dotted_key):
    parts = _parts(dotted_key)
    if not parts:
        return

    current = root
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            return
        current = child

    current.pop(parts[-1], None)


def _convert(value, expected):
    if expected == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.casefold()
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
            return int(value, 10)
        raise ValueError

    if expected == "path":
        if isinstance(value, Path):
            return deepcopy(value)
        if isinstance(value, str):
            return Path(value)
        raise ValueError

    if expected == "csv":
        if not isinstance(value, str):
            raise ValueError
        return [part.strip() for part in value.split(",")]

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


def load(defaults, project, user, environ, explicit, schema, secrets=()):
    layers = [
        ("defaults", deepcopy(defaults)),
        ("project", deepcopy(project)),
        ("user", deepcopy(user) if user is not None else {}),
        ("environ", deepcopy(environ)),
        ("explicit", deepcopy(explicit)),
    ]
    declared = deepcopy(schema)

    prefixes = set()
    for dotted_key in declared:
        parts = _parts(dotted_key)
        for index in range(1, len(parts)):
            prefixes.add(".".join(parts[:index]))

    data = {}
    errors = []
    provenance = {}

    def note(path, source, kind, value):
        provenance.setdefault(path, []).append(
            {
                "source": source,
                "kind": kind,
                "value": deepcopy(value),
            }
        )

    def apply(path, value, source):
        if path not in declared and path not in prefixes:
            errors.append(f"unknown key {path}")
            return

        if isinstance(value, str) and value == DELETE:
            note(path, source, "removed", value)
            _remove(data, path)
            return

        expected = declared.get(path)

        if expected is None:
            if not isinstance(value, dict):
                note(path, source, "invalid", value)
                errors.append(f"invalid {path} from {source}: expected mapping")
                return
            converted = deepcopy(value)
        else:
            try:
                converted = _convert(value, expected)
            except (TypeError, ValueError, OverflowError):
                note(path, source, "invalid", value)
                errors.append(f"invalid {path} from {source}: expected {expected}")
                return

        note(path, source, "valid", converted)

        if isinstance(converted, dict):
            current = _lookup(data, path)
            if current is _MISSING or not isinstance(current, dict):
                _assign(data, path, {})

            for child_key, child_value in converted.items():
                child_path = f"{path}.{child_key}" if path else str(child_key)
                apply(child_path, child_value, source)
        else:
            _assign(data, path, converted)

    for source, layer in layers:
        if source == "environ":
            for dotted_key, value in layer.items():
                apply(str(dotted_key), value, source)
        else:
            for key, value in layer.items():
                apply(str(key), value, source)

    return Settings(data, sorted(errors), provenance, secrets)
