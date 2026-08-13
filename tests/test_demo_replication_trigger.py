"""DEMO replication trigger (Option A, #4834): the loop, after N completed cycles, submits ONE
``copy_with_modifications`` replication request carrying a visible change. FAIL-SAFE + FLAG-GATED
(default OFF), fires exactly once per process, and only PROPOSES (host still gates + executes).

Mirrors the ``_emit_comms_lifecycle`` test style: call the hook as an unbound method on a bare fake
``self`` carrying just the attributes it touches — no full ``Loop`` construction needed.
"""
from __future__ import annotations

import json
import types

from runner.loop import Loop


def _fake(**over):
    """A bare self with the demo-trigger attributes + a recording ``_submit_replication_proposal``."""
    calls = []
    f = types.SimpleNamespace(
        _demo_replication_fired=False,
        _demo_replication_cycles=0,
        _submit_replication_proposal=lambda packet: calls.append(packet) or "HTTP 200",
        _calls=calls,
    )
    for k, v in over.items():
        setattr(f, k, v)
    return f


def test_disabled_by_default(monkeypatch):
    """Flag unset => a complete no-op even after many cycles (the live loop is byte-identical)."""
    monkeypatch.delenv("AQ_DEMO_REPLICATE_AFTER_CYCLES", raising=False)
    f = _fake()
    for _ in range(10):
        Loop._maybe_request_demo_replication(f)
    assert f._calls == []
    assert f._demo_replication_fired is False


def test_fires_exactly_once_after_n_cycles(monkeypatch):
    monkeypatch.setenv("AQ_DEMO_REPLICATE_AFTER_CYCLES", "2")
    monkeypatch.delenv("AQ_DEMO_REPLICATE_MODIFICATION", raising=False)
    monkeypatch.setenv("AQ_INSTANCE_ID", "demo-parent-xyz")
    f = _fake()
    # cycle 1: not yet
    Loop._maybe_request_demo_replication(f)
    assert f._calls == []
    # cycle 2: fires
    Loop._maybe_request_demo_replication(f)
    # cycles 3..6: never again (fired-once)
    for _ in range(4):
        Loop._maybe_request_demo_replication(f)
    assert len(f._calls) == 1, f._calls
    packet = f._calls[0]
    assert packet["mode"] == "copy_with_modifications"
    assert packet["requester_instance_id"] == "demo-parent-xyz"
    # default change is a VISIBLE instance label (allowlisted, echoed by /health/config)
    assert "AQ_INSTANCE_LABEL" in packet["modifications"]
    assert packet["modifications"]["AQ_INSTANCE_LABEL"].startswith("demo-child-")


def test_custom_modification_is_used(monkeypatch):
    monkeypatch.setenv("AQ_DEMO_REPLICATE_AFTER_CYCLES", "1")
    monkeypatch.setenv("AQ_DEMO_REPLICATE_MODIFICATION",
                       json.dumps({"AQ_MISSION_LABEL": "child-mission", "AQ_INSTANCE_LABEL": "L2"}))
    f = _fake()
    Loop._maybe_request_demo_replication(f)
    assert len(f._calls) == 1
    assert f._calls[0]["modifications"] == {"AQ_MISSION_LABEL": "child-mission", "AQ_INSTANCE_LABEL": "L2"}


def test_failsafe_submit_error_never_raises_and_does_not_retry_storm(monkeypatch):
    monkeypatch.setenv("AQ_DEMO_REPLICATE_AFTER_CYCLES", "1")
    boom_calls = {"n": 0}

    def boom(packet):
        boom_calls["n"] += 1
        raise RuntimeError("mgmt API down")

    f = _fake(_submit_replication_proposal=boom)
    # must not raise, and must mark fired so a failure doesn't retry every cycle forever
    for _ in range(5):
        Loop._maybe_request_demo_replication(f)
    assert boom_calls["n"] == 1  # attempted exactly once, then latched off
    assert f._demo_replication_fired is True
