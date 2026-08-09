from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Presentation:
    text: str
    metadata: dict


def _validate_json_value(value: Any, active: set[int] | None = None) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata values must be JSON-compatible")
        return

    if active is None:
        active = set()

    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise ValueError("metadata values must be JSON-compatible")
        active.add(identity)
        try:
            for item in value:
                _validate_json_value(item, active)
        finally:
            active.remove(identity)
        return

    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            raise ValueError("metadata values must be JSON-compatible")
        active.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("metadata values must be JSON-compatible")
                _validate_json_value(item, active)
        finally:
            active.remove(identity)
        return

    raise ValueError("metadata values must be JSON-compatible")


def _value_token(value: Any) -> str:
    _validate_json_value(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def present(message, renderer, capabilities) -> Presentation:
    if isinstance(capabilities, (str, bytes, bytearray)) or not isinstance(
        capabilities, Sequence
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
            if set(capability) != {"kind", "fields"} or not isinstance(
                capability["fields"], list
            ):
                raise ValueError("malformed redact capability")
            fields = capability["fields"]
            if any(not isinstance(field, str) for field in fields):
                raise ValueError("redaction fields must be strings")
            redacted_fields.update(fields)

        elif kind == "legal":
            if set(capability) != {"kind", "text"} or not isinstance(
                capability["text"], str
            ):
                raise ValueError("malformed legal capability")
            legal_notices.add(capability["text"])

        elif kind == "metadata":
            if set(capability) != {"kind", "key", "value"} or not isinstance(
                capability["key"], str
            ):
                raise ValueError("malformed metadata capability")

            key = capability["key"]
            value = capability["value"]
            token = _value_token(value)
            if key in metadata_tokens and metadata_tokens[key] != token:
                raise ValueError(f"conflicting metadata values for key {key!r}")
            if key not in metadata_tokens:
                metadata_tokens[key] = token
                metadata_values[key] = value

        else:
            raise ValueError("unknown capability kind")

    rendered_message = dict(message)
    for field in redacted_fields:
        if field in rendered_message:
            rendered_message[field] = "[REDACTED]"

    body = renderer.render(rendered_message)

    parts: list[str] = []
    if subjects:
        parts.append("\n".join(f"Subject: {text}" for text in sorted(subjects)))
    parts.append(body)
    if legal_notices:
        parts.append("\n".join(sorted(legal_notices)))

    metadata = {key: metadata_values[key] for key in sorted(metadata_values)}
    return Presentation(text="\n".join(parts), metadata=metadata)
