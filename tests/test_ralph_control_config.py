"""Task #4407 — ralph_control instance.yaml fragment validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ralph_portable.ralph_control_config import validate_ralph_control

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs/checkpoints/task_4407/ralph_control.instance.example.yaml"


def test_example_fragment_validates() -> None:
    if not EXAMPLE.exists():
        pytest.skip(
            "example fragment lives in internal checkpoints (docs/checkpoints/task_4407), "
            "curated out of the public repo — internal-only test"
        )
    data = yaml.safe_load(EXAMPLE.read_text())
    errors = validate_ralph_control(data["ralph_control"])
    assert errors == [], errors


def test_disabled_pack_ok() -> None:
    assert validate_ralph_control({"enabled": False}) == []
    assert validate_ralph_control(None) == []


def test_enabled_requires_manager() -> None:
    errors = validate_ralph_control(
        {
            "enabled": True,
            "roles": {},
            "bus": {"kind": "local_mailbox"},
            "ui": {"profile": "stdlib_status"},
            "validation": {"ladder": ["ui", "api", "unit"], "merge_to_main": "manager_gated"},
        }
    )
    assert any("manager_handle" in e for e in errors)


def test_rejects_inverted_ladder() -> None:
    errors = validate_ralph_control(
        {
            "enabled": True,
            "roles": {"manager_handle": "m1"},
            "bus": {"kind": "chat"},
            "ui": {"profile": "cli_only"},
            "validation": {
                "ladder": ["unit", "api", "ui"],
                "merge_to_main": "manager_gated",
            },
        }
    )
    assert any("ladder" in e for e in errors)


def test_rejects_operator_gated_merge() -> None:
    """Cohort→main is manager-gated, not operator-gated."""
    errors = validate_ralph_control(
        {
            "enabled": True,
            "roles": {"manager_handle": "m1"},
            "bus": {"kind": "http_bus"},
            "ui": {"profile": "react_fastapi_management"},
            "validation": {
                "ladder": ["ui", "api", "unit"],
                "merge_to_main": "operator_gated",
            },
        }
    )
    assert any("manager_gated" in e for e in errors)


def test_custom_profile_requires_platforms() -> None:
    errors = validate_ralph_control(
        {
            "enabled": True,
            "roles": {"manager_handle": "m1"},
            "bus": {"kind": "local_mailbox"},
            "ui": {"profile": "custom"},
            "validation": {
                "ladder": ["ui", "api", "unit"],
                "merge_to_main": "manager_gated",
            },
        }
    )
    assert any("platforms" in e for e in errors)


def test_custom_platforms_ok() -> None:
    errors = validate_ralph_control(
        {
            "enabled": True,
            "roles": {"manager_handle": "m1"},
            "bus": {"kind": "local_mailbox"},
            "ui": {"profile": "custom"},
            "platforms": {
                "frontend": "sveltekit",
                "backend": "django",
                "datastore": "postgres_pgvector_age",
            },
            "validation": {
                "ladder": ["ui", "api", "unit"],
                "merge_to_main": "manager_gated",
            },
        }
    )
    assert errors == [], errors
