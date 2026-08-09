from pathlib import Path

import pytest

from runner.config import Instance, Workflow


ROOT = Path(__file__).resolve().parents[1]


def test_default_workflow_v1_is_loaded_by_instance(monkeypatch):
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(ROOT / "workflows"))
    inst = Instance.load(str(ROOT / "templates/running-a-business/instance.yaml"))
    assert inst.workflow.identity == "default/v1"
    assert inst.workflow.stages == ("observe", "decide", "act", "reflect", "learn")
    assert inst.workflow.source.endswith("workflows/default/v1/workflow.yaml")


def test_missing_workflow_selector_fails_loudly(monkeypatch):
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(ROOT / "workflows"))
    with pytest.raises(ValueError, match="workflow plugin not found"):
        Workflow.load("missing/v1")


def test_workflow_selector_rejects_traversal():
    with pytest.raises(ValueError, match="invalid workflow selector"):
        Workflow.load("../default/v1")
