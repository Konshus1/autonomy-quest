from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Iterable


def base_renderer(message):
    """Render a message body into a fresh result mapping."""
    return {"text": message["body"], "metadata": []}


@dataclass(frozen=True)
class _Subject:
    template: str

    def apply(self, message, result):
        block = self.template.format(subject=message["subject"])
        text = f"{block}\n{result['text']}"
        return {"text": text, "metadata": list(result["metadata"])}


@dataclass(frozen=True)
class _Redact:
    fields: tuple
    replacement: str

    def apply(self, message, result):
        text = result["text"]
        for field in self.fields:
            value = message.get(field)
            if isinstance(value, str):
                text = text.replace(value, self.replacement)
        return {"text": text, "metadata": list(result["metadata"])}


@dataclass(frozen=True)
class _Legal:
    text: str

    def apply(self, message, result):
        text = f"{result['text']}\n{self.text}"
        return {"text": text, "metadata": list(result["metadata"])}


@dataclass(frozen=True)
class _Record:
    label: str

    def apply(self, message, result):
        metadata = list(result["metadata"])
        metadata.append(self.label)
        return {"text": result["text"], "metadata": metadata}


def subject(template="Subject: {subject}"):
    """Create a capability that prepends a formatted subject block."""
    return _Subject(template)


def redact(fields: Iterable[str], replacement="[REDACTED]"):
    """Create a capability that redacts values of the selected fields."""
    return _Redact(tuple(fields), replacement)


def legal(text):
    """Create a capability that appends a legal notice."""
    return _Legal(text)


def record(label):
    """Create a capability that appends rendering metadata."""
    return _Record(label)


def make_renderer(renderer: Callable, capabilities):
    """
    Compose a renderer with ordered capabilities.

    Equivalent repeated capabilities are skipped after their first occurrence.
    Capability equivalence is determined by capability type and normalized
    arguments. Neither the caller's message nor a renderer-owned result is
    mutated.
    """
    ordered = []
    for capability in capabilities:
        if not isinstance(capability, (_Subject, _Redact, _Legal, _Record)):
            raise TypeError("unsupported capability")
        if capability not in ordered:
            ordered.append(capability)
    ordered = tuple(ordered)

    def render(message):
        message_copy = deepcopy(message)
        rendered = renderer(message_copy)
        result = {
            "text": rendered["text"],
            "metadata": list(rendered["metadata"]),
        }
        for capability in ordered:
            result = capability.apply(message_copy, result)
        return {"text": result["text"], "metadata": list(result["metadata"])}

    return render
