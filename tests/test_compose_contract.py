from __future__ import annotations

import ast
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_compose_is_two_services_with_prebuilt_database_and_required_ports():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert set(compose["services"]) == {"postgres", "app"}
    postgres = compose["services"]["postgres"]
    assert "image" in postgres and "@sha256:" in postgres["image"]
    assert "build" not in postgres
    assert postgres.get("ports", []) == []  # 5432 is internal by default
    assert set(compose["services"]["app"]["ports"]) == {"127.0.0.1:8080:8080", "127.0.0.1:8090:8090"}


def test_public_dockerfile_does_not_compile_age_or_copy_credentials():
    text = (ROOT / "container/Dockerfile").read_text().lower()
    assert "make -c /tmp/age" not in text
    assert "git clone" not in text
    assert "auth.json" not in text
    assert "copy . " not in text


def test_seed_is_exactly_python_default_dimensions_and_zero_principles():
    tree = ast.parse((ROOT / "ralph_portable/frame_expansion.py").read_text())
    defaults = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "DEFAULT_DIMENSIONS"
            for target in node.targets
        ):
            defaults = ast.literal_eval(node.value)
            break
    assert defaults is not None and len(defaults) == 21
    seed = (ROOT / "schema/seed_structure.sql").read_text()
    assert seed.count("INSERT INTO ralph_frame_dimensions") == 21
    for dimension in defaults:
        assert json.dumps(dimension, separators=(",", ":")) in seed
    assert "INSERT INTO causal_principle" not in seed


def test_default_workflow_is_versioned_and_deterministic():
    workflow = yaml.safe_load((ROOT / "workflows/default/v1/workflow.yaml").read_text())
    assert workflow["name"] == "default"
    assert workflow["version"] == 1
    assert workflow["deterministic"] is True
    assert [stage["id"] for stage in workflow["stages"]] == [
        "observe", "decide", "act", "reflect", "learn"
    ]
