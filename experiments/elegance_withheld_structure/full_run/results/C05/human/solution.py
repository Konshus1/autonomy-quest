"""Deterministic, composable presentation of message records.

Identical subject texts, legal texts, redacted fields, and metadata key/value
pairs are idempotent. Conflicting values for one metadata key are rejected.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from typing import Any


__all__ = ["Presentation", "present"]


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


def _metadata_token(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def present(message: Mapping, renderer: Any, capabilities: Sequence[dict]) -> Presentation:
    """Render a message after validating and order-normalizing capabilities."""
    if not isinstance(message, Mapping):
        raise ValueError("message must be a mapping")
    if not isinstance(capabilities, Sequence) or isinstance(
        capabilities, (str, bytes, bytearray)
    ):
        raise ValueError("capabilities must be a sequence of dictionaries")

    subjects: set[str] = set()
    legal_notices: set[str] = set()
    redacted_fields: set[str] = set()
    metadata_values: dict[str, Any] = {}
    metadata_tokens: dict[str, str] = {}

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

            token = _metadata_token(value)
            if key in metadata_tokens:
                if metadata_tokens[key] != token:
                    raise ValueError(f"conflicting metadata values for key {key!r}")
            else:
                metadata_tokens[key] = token
                metadata_values[key] = deepcopy(value)

        else:
            raise ValueError("unknown capability kind")

    rendered_message = dict(message)
    for field in redacted_fields:
        if field in rendered_message:
            rendered_message[field] = "[REDACTED]"

    body = renderer.render(rendered_message)

    sections: list[str] = []
    if subjects:
        sections.append("\n".join(f"Subject: {text}" for text in sorted(subjects)))
    sections.append(body)
    if legal_notices:
        sections.append("\n".join(sorted(legal_notices)))

    sorted_metadata = {
        key: metadata_values[key]
        for key in sorted(metadata_values)
    }
    return Presentation(text="\n".join(sections), metadata=sorted_metadata)
