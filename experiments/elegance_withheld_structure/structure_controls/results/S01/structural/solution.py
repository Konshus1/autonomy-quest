"""Composable message renderers implemented with the Decorator pattern.

Capabilities are applied in list order. Equivalent duplicate capabilities are
ignored after their first occurrence. Field order within a redact capability is
significant and is processed deterministically from left to right.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Iterable


RenderResult = dict[str, Any]
Renderer = Callable[[dict[str, Any]], RenderResult]


def base_renderer(message: dict[str, Any]) -> RenderResult:
    """Render a message body into a fresh result."""
    return {"text": message["body"], "metadata": []}


def _copy_result(result: RenderResult) -> RenderResult:
    """Validate and copy a renderer result without mutating it."""
    if not isinstance(result, dict):
        raise TypeError("renderer must return a dict")

    text = result.get("text")
    metadata = result.get("metadata")

    if not isinstance(text, str):
        raise TypeError("renderer result 'text' must be a str")
    if not isinstance(metadata, list) or not all(
        isinstance(item, str) for item in metadata
    ):
        raise TypeError("renderer result 'metadata' must be a list[str]")

    return {"text": text, "metadata": list(metadata)}


class _RendererDecorator:
    """Base callable decorator that delegates to one inner renderer."""

    def __init__(self, inner: Renderer) -> None:
        if not callable(inner):
            raise TypeError("inner renderer must be callable")
        self._inner = inner

    def _render_inner(self, message: dict[str, Any]) -> RenderResult:
        # Isolate the inner renderer so it cannot mutate this decorator's view.
        return _copy_result(self._inner(deepcopy(message)))


class _SubjectDecorator(_RendererDecorator):
    def __init__(self, inner: Renderer, template: str) -> None:
        super().__init__(inner)
        self._template = template

    def __call__(self, message: dict[str, Any]) -> RenderResult:
        result = self._render_inner(message)
        heading = self._template.format(subject=message["subject"])
        return {
            "text": heading + "\n" + result["text"],
            "metadata": list(result["metadata"]),
        }


class _RedactDecorator(_RendererDecorator):
    def __init__(
        self,
        inner: Renderer,
        fields: tuple[str, ...],
        replacement: str,
    ) -> None:
        super().__init__(inner)
        self._fields = fields
        self._replacement = replacement

    def __call__(self, message: dict[str, Any]) -> RenderResult:
        result = self._render_inner(message)
        rendered_text = result["text"]

        for field in self._fields:
            value = message.get(field)
            if isinstance(value, str):
                rendered_text = rendered_text.replace(value, self._replacement)

        return {
            "text": rendered_text,
            "metadata": list(result["metadata"]),
        }


class _LegalDecorator(_RendererDecorator):
    def __init__(self, inner: Renderer, notice: str) -> None:
        super().__init__(inner)
        self._notice = notice

    def __call__(self, message: dict[str, Any]) -> RenderResult:
        result = self._render_inner(message)
        return {
            "text": result["text"] + "\n" + self._notice,
            "metadata": list(result["metadata"]),
        }


class _RecordDecorator(_RendererDecorator):
    def __init__(self, inner: Renderer, label: str) -> None:
        super().__init__(inner)
        self._label = label

    def __call__(self, message: dict[str, Any]) -> RenderResult:
        result = self._render_inner(message)
        return {
            "text": result["text"],
            "metadata": list(result["metadata"]) + [self._label],
        }


class _Capability:
    def wrap(self, inner: Renderer) -> Renderer:
        raise NotImplementedError


@dataclass(frozen=True)
class _SubjectCapability(_Capability):
    template: str

    def wrap(self, inner: Renderer) -> Renderer:
        return _SubjectDecorator(inner, self.template)


@dataclass(frozen=True)
class _RedactCapability(_Capability):
    fields: tuple[str, ...]
    replacement: str

    def wrap(self, inner: Renderer) -> Renderer:
        return _RedactDecorator(inner, self.fields, self.replacement)


@dataclass(frozen=True)
class _LegalCapability(_Capability):
    text: str

    def wrap(self, inner: Renderer) -> Renderer:
        return _LegalDecorator(inner, self.text)


@dataclass(frozen=True)
class _RecordCapability(_Capability):
    label: str

    def wrap(self, inner: Renderer) -> Renderer:
        return _RecordDecorator(inner, self.label)


def subject(template: str = "Subject: {subject}") -> _Capability:
    """Create a capability that prepends a formatted subject block."""
    if not isinstance(template, str):
        raise TypeError("template must be a str")
    return _SubjectCapability(template)


def redact(
    fields: Iterable[str], replacement: str = "[REDACTED]"
) -> _Capability:
    """Create a capability that redacts configured message-field values."""
    normalized_fields = (fields,) if isinstance(fields, str) else tuple(fields)
    if not all(isinstance(field, str) for field in normalized_fields):
        raise TypeError("fields must contain only strings")
    if not isinstance(replacement, str):
        raise TypeError("replacement must be a str")
    return _RedactCapability(normalized_fields, replacement)


def legal(text: str) -> _Capability:
    """Create a capability that appends a legal notice on a new line."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    return _LegalCapability(text)


def record(label: str) -> _Capability:
    """Create a capability that appends rendering metadata."""
    if not isinstance(label, str):
        raise TypeError("label must be a str")
    return _RecordCapability(label)


def make_renderer(
    renderer: Renderer, capabilities: Iterable[_Capability]
) -> Renderer:
    """Compose capability decorators in list order.

    Wrapping proceeds from left to right. Since each newly added decorator
    delegates first and then performs its own operation, observable capability
    effects occur in the supplied order. The first occurrence of an equivalent
    frozen capability wins; later equivalent occurrences are ignored.
    """
    if not callable(renderer):
        raise TypeError("renderer must be callable")

    composed = renderer
    seen: set[_Capability] = set()

    for capability in capabilities:
        if not isinstance(capability, _Capability):
            raise TypeError("capabilities must be values returned by capability functions")
        if capability in seen:
            continue
        seen.add(capability)
        composed = capability.wrap(composed)

    def render(message: dict[str, Any]) -> RenderResult:
        if not isinstance(message, dict):
            raise TypeError("message must be a dict")
        protected_message = deepcopy(message)
        return _copy_result(composed(protected_message))

    return render
