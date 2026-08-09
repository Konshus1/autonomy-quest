"""Composable message rendering using the Decorator pattern.

Capabilities are applied from left to right. Equivalent repeated capabilities are
ignored after their first occurrence, making duplicate application deterministic.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Iterable


RenderResult = dict[str, object]
Renderer = Callable[[dict], RenderResult]


def base_renderer(message: dict) -> RenderResult:
    """Render a message body without mutating the message."""
    return {"text": deepcopy(message["body"]), "metadata": []}


@dataclass(frozen=True)
class _SubjectCapability:
    template: str


@dataclass(frozen=True)
class _RedactCapability:
    fields: tuple[str, ...]
    replacement: str


@dataclass(frozen=True)
class _LegalCapability:
    text: str


@dataclass(frozen=True)
class _RecordCapability:
    label: str


def subject(template: str = "Subject: {subject}") -> _SubjectCapability:
    """Return a capability that prepends a formatted subject block."""
    if not isinstance(template, str):
        raise TypeError("template must be a string")
    return _SubjectCapability(template)


def redact(
    fields: Iterable[str], replacement: str = "[REDACTED]"
) -> _RedactCapability:
    """Return a capability that redacts values from named message fields."""
    if isinstance(fields, str):
        normalized = (fields,)
    else:
        normalized = tuple(fields)
    if not all(isinstance(field, str) for field in normalized):
        raise TypeError("fields must contain only strings")
    if not isinstance(replacement, str):
        raise TypeError("replacement must be a string")
    return _RedactCapability(normalized, replacement)


def legal(text: str) -> _LegalCapability:
    """Return a capability that appends a legal notice."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return _LegalCapability(text)


def record(label: str) -> _RecordCapability:
    """Return a capability that records rendering metadata."""
    if not isinstance(label, str):
        raise TypeError("label must be a string")
    return _RecordCapability(label)


def _copy_result(result: RenderResult) -> RenderResult:
    if not isinstance(result, dict):
        raise TypeError("renderer must return a dict")
    try:
        text = result["text"]
        metadata = result["metadata"]
    except KeyError as exc:
        raise TypeError("renderer result must contain text and metadata") from exc
    if not isinstance(text, str):
        raise TypeError("renderer result text must be a string")
    if not isinstance(metadata, list) or not all(
        isinstance(item, str) for item in metadata
    ):
        raise TypeError("renderer result metadata must be a list of strings")
    return {"text": text, "metadata": list(metadata)}


class _ProtectedRenderer:
    """Protect the caller's message and normalize the renderer's result."""

    def __init__(self, renderer: Renderer) -> None:
        self._renderer = renderer

    def __call__(self, message: dict) -> RenderResult:
        return _copy_result(self._renderer(deepcopy(message)))


class _Decorator:
    """Base class for composable renderer decorators."""

    def __init__(self, inner: Renderer) -> None:
        self._inner = inner


class _SubjectDecorator(_Decorator):
    def __init__(self, inner: Renderer, template: str) -> None:
        super().__init__(inner)
        self._template = template

    def __call__(self, message: dict) -> RenderResult:
        result = _copy_result(self._inner(message))
        block = self._template.format(subject=message["subject"])
        result["text"] = block + "\n" + result["text"]
        return result


class _RedactDecorator(_Decorator):
    def __init__(
        self, inner: Renderer, fields: tuple[str, ...], replacement: str
    ) -> None:
        super().__init__(inner)
        self._fields = fields
        self._replacement = replacement

    def __call__(self, message: dict) -> RenderResult:
        result = _copy_result(self._inner(message))
        current_text = result["text"]
        for field in self._fields:
            value = message.get(field)
            if isinstance(value, str):
                current_text = current_text.replace(value, self._replacement)
        result["text"] = current_text
        return result


class _LegalDecorator(_Decorator):
    def __init__(self, inner: Renderer, notice: str) -> None:
        super().__init__(inner)
        self._notice = notice

    def __call__(self, message: dict) -> RenderResult:
        result = _copy_result(self._inner(message))
        result["text"] = result["text"] + "\n" + self._notice
        return result


class _RecordDecorator(_Decorator):
    def __init__(self, inner: Renderer, label: str) -> None:
        super().__init__(inner)
        self._label = label

    def __call__(self, message: dict) -> RenderResult:
        result = _copy_result(self._inner(message))
        result["metadata"].append(self._label)
        return result


def make_renderer(renderer: Renderer, capabilities: Iterable[object]) -> Renderer:
    """Compose capabilities in list order around a renderer.

    The first occurrence of each equivalent capability is used; later equivalent
    occurrences are ignored. The returned callable protects both its input and
    every result obtained from the supplied renderer.
    """
    if not callable(renderer):
        raise TypeError("renderer must be callable")

    composed: Renderer = _ProtectedRenderer(renderer)
    seen: set[object] = set()

    for capability in capabilities:
        if capability in seen:
            continue
        seen.add(capability)

        if isinstance(capability, _SubjectCapability):
            composed = _SubjectDecorator(composed, capability.template)
        elif isinstance(capability, _RedactCapability):
            composed = _RedactDecorator(
                composed, capability.fields, capability.replacement
            )
        elif isinstance(capability, _LegalCapability):
            composed = _LegalDecorator(composed, capability.text)
        elif isinstance(capability, _RecordCapability):
            composed = _RecordDecorator(composed, capability.label)
        else:
            raise TypeError("unsupported capability")

    return composed
