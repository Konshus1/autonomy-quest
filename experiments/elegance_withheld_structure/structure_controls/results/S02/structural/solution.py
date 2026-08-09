from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

Processor = Callable[[Mapping[str, Any]], dict[str, Any]]


def base_processor(record: Mapping[str, Any]) -> dict[str, Any]:
    return {"value": str(record["value"]), "trace": []}


def _copy_result(result: object) -> dict[str, Any]:
    if type(result) is not dict or set(result) != {"value", "trace"}:
        raise TypeError("processor must return exactly 'value' and 'trace'")

    value = result["value"]
    trace = result["trace"]
    if not isinstance(value, str):
        raise TypeError("processor result 'value' must be a string")
    if not isinstance(trace, list) or any(not isinstance(item, str) for item in trace):
        raise TypeError("processor result 'trace' must be a list of strings")

    return {"value": value, "trace": list(trace)}


class _ProcessorDecorator:
    def __init__(self, inner: Processor) -> None:
        if not callable(inner):
            raise TypeError("inner processor must be callable")
        self._inner = inner

    def _result(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return _copy_result(self._inner(record))


class _PrefixDecorator(_ProcessorDecorator):
    def __init__(self, inner: Processor, text: str) -> None:
        super().__init__(inner)
        self._text = text

    def __call__(self, record: Mapping[str, Any]) -> dict[str, Any]:
        result = self._result(record)
        return {"value": self._text + result["value"], "trace": list(result["trace"])}


class _ReplaceDecorator(_ProcessorDecorator):
    def __init__(self, inner: Processor, old: str, new: str) -> None:
        super().__init__(inner)
        self._old = old
        self._new = new

    def __call__(self, record: Mapping[str, Any]) -> dict[str, Any]:
        result = self._result(record)
        return {
            "value": result["value"].replace(self._old, self._new),
            "trace": list(result["trace"]),
        }


class _SuffixDecorator(_ProcessorDecorator):
    def __init__(self, inner: Processor, text: str) -> None:
        super().__init__(inner)
        self._text = text

    def __call__(self, record: Mapping[str, Any]) -> dict[str, Any]:
        result = self._result(record)
        return {"value": result["value"] + self._text, "trace": list(result["trace"])}


class _AuditDecorator(_ProcessorDecorator):
    def __init__(self, inner: Processor, label: str) -> None:
        super().__init__(inner)
        self._label = label

    def __call__(self, record: Mapping[str, Any]) -> dict[str, Any]:
        result = self._result(record)
        return {"value": result["value"], "trace": [*result["trace"], self._label]}


class _Capability:
    def wrap(self, inner: Processor) -> Processor:
        raise NotImplementedError


@dataclass(frozen=True)
class _Prefix(_Capability):
    text: str

    def wrap(self, inner: Processor) -> Processor:
        return _PrefixDecorator(inner, self.text)


@dataclass(frozen=True)
class _Replace(_Capability):
    old: str
    new: str

    def wrap(self, inner: Processor) -> Processor:
        return _ReplaceDecorator(inner, self.old, self.new)


@dataclass(frozen=True)
class _Suffix(_Capability):
    text: str

    def wrap(self, inner: Processor) -> Processor:
        return _SuffixDecorator(inner, self.text)


@dataclass(frozen=True)
class _Audit(_Capability):
    label: str

    def wrap(self, inner: Processor) -> Processor:
        return _AuditDecorator(inner, self.label)


def _require_string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def prefix(text: str) -> _Capability:
    return _Prefix(_require_string(text, "text"))


def replace(old: str, new: str) -> _Capability:
    return _Replace(_require_string(old, "old"), _require_string(new, "new"))


def suffix(text: str) -> _Capability:
    return _Suffix(_require_string(text, "text"))


def audit(label: str) -> _Capability:
    return _Audit(_require_string(label, "label"))


def make_processor(processor: Processor, capabilities: Iterable[_Capability]) -> Processor:
    if not callable(processor):
        raise TypeError("processor must be callable")

    current = processor
    seen: set[_Capability] = set()

    try:
        iterator = iter(capabilities)
    except TypeError as error:
        raise TypeError("capabilities must be iterable") from error

    for capability in iterator:
        if not isinstance(capability, _Capability):
            raise TypeError(f"unknown capability: {capability!r}")
        if capability in seen:
            continue
        seen.add(capability)
        current = capability.wrap(current)

    return current
