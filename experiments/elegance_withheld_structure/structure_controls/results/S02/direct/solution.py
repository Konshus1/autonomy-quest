"""Composable record-value processors implemented with the Decorator pattern."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

Processor = Callable[[Mapping[str, Any]], dict[str, Any]]


def base_processor(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the record value as text with an empty audit trace."""
    return {"value": str(record["value"]), "trace": []}


def _copy_result(result: object) -> dict[str, Any]:
    """Validate and copy a processor result without mutating the original."""
    if not isinstance(result, dict):
        raise TypeError("processor result must be a dict")
    if set(result) != {"value", "trace"}:
        raise ValueError("processor result must contain exactly 'value' and 'trace'")

    value = result["value"]
    trace = result["trace"]
    if not isinstance(value, str):
        raise TypeError("processor result 'value' must be a str")
    if not isinstance(trace, list) or not all(isinstance(item, str) for item in trace):
        raise TypeError("processor result 'trace' must be a list[str]")

    return {"value": value, "trace": list(trace)}


class _ProcessorDecorator(ABC):
    """Base decorator sharing the public processor callable contract."""

    def __init__(self, inner: Processor) -> None:
        self._inner = inner

    def __call__(self, record: Mapping[str, Any]) -> dict[str, Any]:
        result = _copy_result(self._inner(record))
        return self._apply(result)

    @abstractmethod
    def _apply(self, result: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class _PrefixDecorator(_ProcessorDecorator):
    def __init__(self, inner: Processor, text: str) -> None:
        super().__init__(inner)
        self._text = text

    def _apply(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"value": self._text + result["value"], "trace": list(result["trace"])}


class _ReplaceDecorator(_ProcessorDecorator):
    def __init__(self, inner: Processor, old: str, new: str) -> None:
        super().__init__(inner)
        self._old = old
        self._new = new

    def _apply(self, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "value": result["value"].replace(self._old, self._new),
            "trace": list(result["trace"]),
        }


class _SuffixDecorator(_ProcessorDecorator):
    def __init__(self, inner: Processor, text: str) -> None:
        super().__init__(inner)
        self._text = text

    def _apply(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"value": result["value"] + self._text, "trace": list(result["trace"])}


class _AuditDecorator(_ProcessorDecorator):
    def __init__(self, inner: Processor, label: str) -> None:
        super().__init__(inner)
        self._label = label

    def _apply(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"value": result["value"], "trace": [*result["trace"], self._label]}


@dataclass(frozen=True)
class _Capability(ABC):
    @abstractmethod
    def wrap(self, inner: Processor) -> Processor:
        raise NotImplementedError


@dataclass(frozen=True)
class _PrefixCapability(_Capability):
    text: str

    def wrap(self, inner: Processor) -> Processor:
        return _PrefixDecorator(inner, self.text)


@dataclass(frozen=True)
class _ReplaceCapability(_Capability):
    old: str
    new: str

    def wrap(self, inner: Processor) -> Processor:
        return _ReplaceDecorator(inner, self.old, self.new)


@dataclass(frozen=True)
class _SuffixCapability(_Capability):
    text: str

    def wrap(self, inner: Processor) -> Processor:
        return _SuffixDecorator(inner, self.text)


@dataclass(frozen=True)
class _AuditCapability(_Capability):
    label: str

    def wrap(self, inner: Processor) -> Processor:
        return _AuditDecorator(inner, self.label)


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    return value


def prefix(text: str) -> _Capability:
    return _PrefixCapability(_require_text(text, "text"))


def replace(old: str, new: str) -> _Capability:
    return _ReplaceCapability(
        _require_text(old, "old"),
        _require_text(new, "new"),
    )


def suffix(text: str) -> _Capability:
    return _SuffixCapability(_require_text(text, "text"))


def audit(label: str) -> _Capability:
    return _AuditCapability(_require_text(label, "label"))


def make_processor(processor: Processor, capabilities: Iterable[_Capability]) -> Processor:
    """Compose unique capabilities in their supplied list order."""
    if not callable(processor):
        raise TypeError("processor must be callable")

    try:
        requested = list(capabilities)
    except TypeError as exc:
        raise TypeError("capabilities must be iterable") from exc

    composed = processor
    seen: set[_Capability] = set()
    for capability in requested:
        if not isinstance(capability, _Capability):
            raise TypeError(f"unknown capability: {capability!r}")
        if capability in seen:
            continue
        seen.add(capability)
        composed = capability.wrap(composed)

    return composed


__all__ = [
    "base_processor",
    "prefix",
    "replace",
    "suffix",
    "audit",
    "make_processor",
]
