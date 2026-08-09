"""Composable message renderers.

Duplicate capabilities are deterministic: an equivalent capability (the same kind
and equal configuration) is applied only at its first position in the list.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Iterable


RenderResult = dict[str, object]
Renderer = Callable[[dict], RenderResult]


@dataclass(frozen=True)
class _Subject:
    template: str


@dataclass(frozen=True)
class _Redact:
    fields: tuple[str, ...]
    replacement: str


@dataclass(frozen=True)
class _Legal:
    text: str


@dataclass(frozen=True)
class _Record:
    label: str


def base_renderer(message: dict) -> RenderResult:
    """Render a message body with fresh result containers."""
    return {"text": deepcopy(message["body"]), "metadata": []}


def subject(template: str = "Subject: {subject}") -> _Subject:
    """Create a capability that prepends a formatted subject block."""
    return _Subject(template)


def redact(
    fields: Iterable[str], replacement: str = "[REDACTED]"
) -> _Redact:
    """Create a capability that redacts values from the specified fields."""
    if isinstance(fields, str):
        fields = (fields,)
    return _Redact(tuple(fields), replacement)


def legal(text: str) -> _Legal:
    """Create a capability that appends a legal notice."""
    return _Legal(text)


def record(label: str) -> _Record:
    """Create a capability that records rendering metadata."""
    return _Record(label)


def make_renderer(renderer: Renderer, capabilities: Iterable[object]) -> Renderer:
    """Return a renderer applying unique capabilities in list order."""
    unique = []
    for capability in capabilities:
        if not isinstance(capability, (_Subject, _Redact, _Legal, _Record)):
            raise TypeError("unknown rendering capability")
        if capability not in unique:
            unique.append(capability)

    def render(message: dict) -> RenderResult:
        capability_message = deepcopy(message)
        renderer_message = deepcopy(message)
        rendered = renderer(renderer_message)

        text = rendered["text"]
        metadata = list(rendered["metadata"])

        if not isinstance(text, str):
            raise TypeError("renderer result 'text' must be a string")
        if not isinstance(rendered["metadata"], list):
            raise TypeError("renderer result 'metadata' must be a list")
        if not all(isinstance(item, str) for item in metadata):
            raise TypeError("renderer metadata entries must be strings")

        for capability in unique:
            if isinstance(capability, _Subject):
                block = capability.template.format(
                    subject=capability_message["subject"]
                )
                text = block + "\n" + text
            elif isinstance(capability, _Redact):
                for field in capability.fields:
                    value = capability_message.get(field)
                    if isinstance(value, str):
                        text = text.replace(value, capability.replacement)
            elif isinstance(capability, _Legal):
                text = text + "\n" + capability.text
            else:
                metadata.append(capability.label)

        return {"text": text, "metadata": metadata}

    return render
