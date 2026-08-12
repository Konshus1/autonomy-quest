"""Role declaration parsing + activation (#4834 comms Phase 4, runner/role_config.py).

The workflow LAYER declares roles; a malformed declaration fails LOUD at load (never silently
activates or falls back to a surprise). Activation needs BOTH roles and the flag.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from runner.role_config import (
    HARD_FANOUT_CEILING,
    RoleConfig,
    RoleAgent,
    multi_agent_active,
    resolve_roles,
)


def _wf(tmp_path, body: str, identity="wf/v1"):
    p = tmp_path / "workflow.yaml"
    p.write_text(body)
    return SimpleNamespace(identity=identity, source=str(p))


def test_no_roles_block_is_none(tmp_path):
    wf = _wf(tmp_path, "name: wf\nversion: 1\n")
    assert resolve_roles(wf) is None


def test_default_v1_short_circuits_without_reading_source():
    # source deliberately points nowhere; default/v1 must not even read it.
    wf = SimpleNamespace(identity="default/v1", source="/does/not/exist.yaml")
    assert resolve_roles(wf) is None


def test_valid_roles_parse(tmp_path):
    wf = _wf(tmp_path, """
name: wf
version: 1
roles:
  fanout_cap: 3
  delivery: subprocess
  agents:
    planner: {model: p}
    coder: {model: c}
    reviewer: {model: r, independent: true}
""")
    cfg = resolve_roles(wf)
    assert isinstance(cfg, RoleConfig)
    assert cfg.fanout_cap == 3
    assert set(cfg.agents) == {"planner", "coder", "reviewer"}
    assert cfg.producer_role() == "coder"
    assert cfg.reviewer_roles() == ("reviewer",)
    assert cfg.agents["reviewer"].independent is True


def test_unknown_role_rejected(tmp_path):
    wf = _wf(tmp_path, """
name: wf
version: 1
roles:
  agents:
    shell: {model: x}
""")
    with pytest.raises(ValueError, match="unknown role"):
        resolve_roles(wf)


def test_fanout_over_ceiling_rejected(tmp_path):
    wf = _wf(tmp_path, f"""
name: wf
version: 1
roles:
  fanout_cap: {HARD_FANOUT_CEILING + 1}
  agents:
    coder: {{model: c}}
""")
    with pytest.raises(ValueError, match="ceiling"):
        resolve_roles(wf)


def test_unknown_delivery_rejected(tmp_path):
    wf = _wf(tmp_path, """
name: wf
version: 1
roles:
  delivery: carrier-pigeon
  agents:
    coder: {model: c}
""")
    with pytest.raises(ValueError, match="delivery"):
        resolve_roles(wf)


def test_no_producer_role_rejected(tmp_path):
    # only a reviewer, no coder/planner => cannot produce a decision
    wf = _wf(tmp_path, """
name: wf
version: 1
roles:
  agents:
    reviewer: {model: r}
""")
    with pytest.raises(ValueError, match="coder' or 'planner'"):
        resolve_roles(wf)


def test_activation_needs_roles_and_flag(tmp_path, monkeypatch):
    wf = _wf(tmp_path, """
name: wf
version: 1
roles:
  agents:
    coder: {model: c}
""")
    monkeypatch.delenv("AQ_WORKFLOW_MULTI_AGENT", raising=False)
    assert multi_agent_active(wf) is False
    monkeypatch.setenv("AQ_WORKFLOW_MULTI_AGENT", "1")
    assert multi_agent_active(wf) is True
    # a workflow with no roles is inert even with the flag on
    empty = _wf(tmp_path / "sub" if False else tmp_path, "name: wf\nversion: 1\n")
    assert multi_agent_active(empty) is False
