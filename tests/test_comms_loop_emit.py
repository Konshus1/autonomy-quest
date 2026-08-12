"""Loop comms-emit: FAIL-SAFE + FLAG-GATED default-OFF byte-identical (#4834 comms Phase 2).

Same discipline as the causal-consult / self-correction / heartbeat hooks:
  * default OFF => a complete no-op that touches NO network (the live default loop is byte-identical);
  * ON but the local API failing => the emit swallows the error and returns None (never raises);
  * the loop's ``_emit_comms_lifecycle`` swallows even an emit-layer bug (belt-and-suspenders), so a
    comms problem can never crash or alter the loop.
"""

from __future__ import annotations

import types

import pytest

from runner import comms_emit


REPLICA_ENV = {
    "AQ_COMMS_INSTANCE_TOKEN": "tok", "AQ_INSTANCE_ID": "urn:uuid:self",
    "AQ_COMMS_PARENT_INSTANCE_ID": "urn:uuid:parent", "AQ_MGMT_PORT": "8090",
}


def _exploding_opener(*args, **kwargs):
    raise AssertionError("emit must NOT touch the network when the flag is OFF")


# --- flag gate: default OFF is byte-identical (no network) --------------------
def test_emit_disabled_by_default():
    assert comms_emit.emit_enabled({}) is False


def test_flag_off_makes_emit_a_noop_with_no_network():
    env = {**REPLICA_ENV}  # AQ_COMMS_EMIT unset
    # An opener that explodes if called proves the OFF path never even attempts a request.
    out = comms_emit.emit_cycle_completed(
        cycle=types.SimpleNamespace(run_id=1, work_id=2), template="t", workflow="w",
        env=env, opener=_exploding_opener)
    assert out is None


def test_flag_on_but_no_credential_is_noop():
    env = {"AQ_COMMS_EMIT": "1"}  # enabled but unconfigured (no token/instance) => no-op
    assert comms_emit.emit_cycle_completed(env=env, opener=_exploding_opener) is None


# --- fail-safe: an emit failure NEVER raises ---------------------------------
def test_emit_swallows_api_failure():
    env = {**REPLICA_ENV, "AQ_COMMS_EMIT": "1"}

    def failing_opener(req, *, timeout_s):
        raise OSError("connection refused")

    # Returns None instead of propagating — the loop must never notice.
    assert comms_emit.emit_status_report(text="x", env=env, opener=failing_opener) is None
    assert comms_emit.emit_cycle_completed(
        cycle=types.SimpleNamespace(run_id=1, work_id=2), env=env, opener=failing_opener) is None


def test_emit_swallows_bad_payload():
    env = {**REPLICA_ENV, "AQ_COMMS_EMIT": "1"}
    # An oversize summary would raise PayloadError inside; the fail-safe wrapper swallows it.
    assert comms_emit.emit_experiment_result(
        summary="x" * (1 << 20), env=env, opener=_exploding_opener) is None


# --- ON path actually posts (with a captured opener) -------------------------
def test_flag_on_emits_a_status_report():
    env = {**REPLICA_ENV, "AQ_COMMS_EMIT": "1"}
    seen = {}

    def capture(req, *, timeout_s):
        import json
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization")
        seen["body"] = json.loads(req.data)
        return b'{"ok": true, "duplicate": false}'

    out = comms_emit.emit_cycle_completed(
        cycle=types.SimpleNamespace(run_id=7, work_id=9), template="baseline", workflow="default",
        env=env, opener=capture)
    assert out == {"ok": True, "duplicate": False}
    assert seen["url"] == "http://127.0.0.1:8090/api/agent-comms"
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["kind"] == "status.report"
    assert seen["body"]["channel"] == "lineage/urn:uuid:parent/status"
    # A stable idempotency key so an at-least-once retry does not duplicate.
    assert seen["body"]["idempotency_key"] == "cycle:urn:uuid:self:7:9"


# --- loop-level guard: _emit_comms_lifecycle never raises --------------------
def test_loop_lifecycle_hook_is_failsafe(monkeypatch):
    from runner.loop import Loop

    # A bare object with just the attributes the hook reads — avoids constructing a full Loop.
    fake = types.SimpleNamespace(inst=types.SimpleNamespace(template="t"), workflow_id="w")

    # Force emit_enabled True and make the emit blow up: the hook must still swallow it.
    monkeypatch.setattr(comms_emit, "emit_enabled", lambda *a, **k: True)

    def boom(*a, **k):
        raise RuntimeError("emit layer bug")

    monkeypatch.setattr(comms_emit, "emit_cycle_completed", boom)
    # Bound-method call on the fake instance: it must return None, not raise.
    Loop._emit_comms_lifecycle(fake, types.SimpleNamespace(run_id=1, work_id=2))


def test_loop_lifecycle_hook_noop_when_disabled(monkeypatch):
    from runner.loop import Loop

    fake = types.SimpleNamespace(inst=types.SimpleNamespace(template="t"), workflow_id="w")
    called = {"n": 0}
    monkeypatch.setattr(comms_emit, "emit_enabled", lambda *a, **k: False)
    monkeypatch.setattr(comms_emit, "emit_cycle_completed",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    Loop._emit_comms_lifecycle(fake, None)
    assert called["n"] == 0  # disabled => emit_cycle_completed is never reached
