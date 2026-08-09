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


def _is_json_compatible(value: Any, seen: set[int] | None = None) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)

    if seen is None:
        seen = set()

    if isinstance(value, list):
        identity = id(value)
        if identity in seen:
            return False
        seen.add(identity)
        try:
            return all(_is_json_compatible(item, seen) for item in value)
        finally:
            seen.remove(identity)

    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            return False
        if not all(isinstance(key, str) for key in value):
            return False
        seen.add(identity)
        try:
            return all(_is_json_compatible(item, seen) for item in value.values())
        finally:
            seen.remove(identity)

    return False


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def present(message: Mapping[str, Any], renderer: Any, capabilities: Sequence[dict]) -> Presentation:
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
    metadata: dict[str, Any] = {}
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
                raise ValueError("redact fields must be a list of strings")
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
                raise ValueError("metadata requires a string key and JSON-compatible value")

            encoding = _canonical_json(value)
            if key in metadata_encodings:
                if metadata_encodings[key] != encoding:
                    raise ValueError(f"conflicting metadata values for key {key!r}")
            else:
                metadata_encodings[key] = encoding
                metadata[key] = value

        else:
            raise ValueError(f"unknown capability kind: {kind!r}")

    rendered_message = dict(message)
    for field in sorted(redacted_fields):
        rendered_message[field] = "[REDACTED]"

    body = renderer.render(rendered_message)

    sections: list[str] = []
    if subjects:
        sections.append("\n".join(f"Subject: {text}" for text in sorted(subjects)))
    sections.append(body)
    if legal_notices:
        sections.append("\n".join(sorted(legal_notices)))

    sorted_metadata = {key: metadata[key] for key in sorted(metadata)}
    return Presentation(text="\n".join(sections), metadata=sorted_metadata)
