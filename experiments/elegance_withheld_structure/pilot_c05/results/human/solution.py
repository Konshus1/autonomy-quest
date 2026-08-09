from __future__ import annotations

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
    return {"text": deepcopy(message["body"]), "metadata": []}


def subject(template: str = "Subject: {subject}") -> _Subject:
    return _Subject(template)


def redact(fields: Iterable[str], replacement: str = "[REDACTED]") -> _Redact:
    if isinstance(fields, str):
        fields = (fields,)
    return _Redact(tuple(fields), replacement)


def legal(text: str) -> _Legal:
    return _Legal(text)


def record(label: str) -> _Record:
    return _Record(label)


def make_renderer(renderer: Renderer, capabilities: Iterable[object]) -> Renderer:
    selected = []
    for capability in capabilities:
        if not isinstance(capability, (_Subject, _Redact, _Legal, _Record)):
            raise TypeError(f"Unsupported capability: {capability!r}")
        if capability not in selected:
            selected.append(capability)

    def render(message: dict) -> RenderResult:
        message_copy = deepcopy(message)
        rendered = renderer(message_copy)

        text = rendered["text"]
        metadata = list(deepcopy(rendered["metadata"]))

        for capability in selected:
            if isinstance(capability, _Subject):
                block = capability.template.format(**message_copy)
                text = f"{block}\n{text}"
            elif isinstance(capability, _Redact):
                for field in capability.fields:
                    value = message_copy.get(field)
                    if isinstance(value, str):
                        text = text.replace(value, capability.replacement)
            elif isinstance(capability, _Legal):
                text = f"{text}\n{capability.text}"
            elif isinstance(capability, _Record):
                metadata.append(capability.label)

        return {"text": text, "metadata": metadata}

    return render
