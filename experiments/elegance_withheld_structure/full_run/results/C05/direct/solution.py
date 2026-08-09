from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Presentation:
    text: str
    metadata: dict


def _is_json_compatible(value: Any, active: set[int] | None = None) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)

    if active is None:
        active = set()

    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            return False
        active.add(identity)
        try:
            return all(_is_json_compatible(item, active) for item in value)
        finally:
            active.remove(identity)

    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            return False
        if not all(isinstance(key, str) for key in value):
            return False
        active.add(identity)
        try:
            return all(_is_json_compatible(item, active) for item in value.values())
        finally:
            active.remove(identity)

    return False


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def present(message: Mapping, renderer: Any, capabilities: Sequence[dict]) -> Presentation:
    if not isinstance(message, Mapping):
        raise ValueError("message must be a mapping")
    if (
        not isinstance(capabilities, Sequence)
        or isinstance(capabilities, (str, bytes, bytearray))
    ):
        raise ValueError("capabilities must be a sequence of dictionaries")

    subjects: set[str] = set()
    legal_notices: set[str] = set()
    redacted_fields: set[str] = set()
    metadata_values: dict[str, Any] = {}
    metadata_encodings: dict[str, str] = {}

    for capability in capabilities:
        if not isinstance(capability, dict):
            raise ValueError("each capability must be a dictionary")

        kind = capability.get("kind")

        if kind == "subject":
            if set(capability) != {"kind", "text"} or not isinstance(
                capability["text"], str
            ):
                raise ValueError("malformed subject capability")
            subjects.add(capability["text"])

        elif kind == "redact":
            if set(capability) != {"kind", "fields"}:
                raise ValueError("malformed redact capability")
            fields = capability["fields"]
            if not isinstance(fields, list) or not all(
                isinstance(field, str) for field in fields
            ):
                raise ValueError("malformed redact capability")
            redacted_fields.update(fields)

        elif kind == "legal":
            if set(capability) != {"kind", "text"} or not isinstance(
                capability["text"], str
            ):
                raise ValueError("malformed legal capability")
            legal_notices.add(capability["text"])

        elif kind == "metadata":
            if set(capability) != {"kind", "key", "value"}:
                raise ValueError("malformed metadata capability")
            key = capability["key"]
            value = capability["value"]
            if not isinstance(key, str) or not _is_json_compatible(value):
                raise ValueError("malformed metadata capability")

            encoding = _canonical_json(value)
            if key in metadata_encodings:
                if metadata_encodings[key] != encoding:
                    raise ValueError(f"conflicting metadata values for key {key!r}")
            else:
                metadata_encodings[key] = encoding
                metadata_values[key] = value

        else:
            raise ValueError("unknown capability kind")

    rendered_message = dict(message)
    for field in redacted_fields:
        if field in rendered_message:
            rendered_message[field] = "[REDACTED]"

    body = renderer.render(rendered_message)
    if not isinstance(body, str):
        raise TypeError("renderer.render(message) must return str")

    sections: list[str] = []
    if subjects:
        sections.append("\n".join(f"Subject: {text}" for text in sorted(subjects)))
    sections.append(body)
    if legal_notices:
        sections.append("\n".join(sorted(legal_notices)))

    metadata = {key: metadata_values[key] for key in sorted(metadata_values)}
    return Presentation(text="\n".join(sections), metadata=metadata)
