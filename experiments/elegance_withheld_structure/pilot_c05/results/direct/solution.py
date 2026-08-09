"""Composable message renderers."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Iterable, List, Mapping, TypedDict


class RenderResult(TypedDict):
    text: str
    metadata: List[str]


Renderer = Callable[[dict], RenderResult]


@dataclass(frozen=True)
class _Subject:
    template: str


@dataclass(frozen=True)
class _Redact:
    fields: tuple
    replacement: str


@dataclass(frozen=True)
class _Legal:
    text: str


@dataclass(frozen=True)
class _Record:
    label: str


def base_renderer(message: Mapping) -> RenderResult:
    """Render a message body with fresh result containers."""
    return {"text": deepcopy(message["body"]), "metadata": []}


def subject(template: str = "Subject: {subject}") -> _Subject:
    """Create a capability that prepends a formatted subject line."""
    return _Subject(template)


def redact(fields: Iterable[str], replacement: str = "[REDACTED]") -> _Redact:
    """Create a capability that redacts values of the named fields."""
    return _Redact(tuple(fields), replacement)


def legal(text: str) -> _Legal:
    """Create a capability that appends a legal notice."""
    return _Legal(text)


def record(label: str) -> _Record:
    """Create a capability that records rendering metadata."""
    return _Record(label)


def make_renderer(renderer: Renderer, capabilities: Iterable[object]) -> Renderer:
    """Compose a renderer with ordered, deduplicated capabilities."""
    ordered = []
    for capability in capabilities:
        if not isinstance(capability, (_Subject, _Redact, _Legal, _Record)):
            raise TypeError("unknown rendering capability")
        if capability not in ordered:
            ordered.append(capability)
    ordered = tuple(ordered)

    def composed(message: dict) -> RenderResult:
        message_copy = deepcopy(message)
        rendered = renderer(message_copy)

        text = rendered["text"]
        metadata = list(rendered["metadata"])

        for capability in ordered:
            if isinstance(capability, _Subject):
                heading = capability.template.format(subject=message_copy["subject"])
                text = heading + "\n" + text
            elif isinstance(capability, _Redact):
                for field in capability.fields:
                    value = message_copy.get(field)
                    if isinstance(value, str):
                        text = text.replace(value, capability.replacement)
            elif isinstance(capability, _Legal):
                text = text + "\n" + capability.text
            else:
                metadata.append(capability.label)

        return {"text": text, "metadata": metadata}

    return composed
