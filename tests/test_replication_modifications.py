"""Bounded guest-modification policy (#4834 Step 2 — ``copy_with_modifications``).

The load-bearing invariant proved here: NO modification (config or code) a guest requests can
(a) weaken the replica's OWN replication safety gates (cap/memory/opt-in/host-executes),
(b) grant the replica or its guest docker.sock, or (c) set the replica to auto-replicate. A
modified replica stays bounded exactly like a straight_copy.

Each rejection is proved at BOTH propose-time and host apply-time (defense in depth: the host
re-validates the stored packet and never trusts it), including a TAMPERED stored packet that
bypassed propose.
"""

from __future__ import annotations

import subprocess

import pytest

from ralph_portable import replication_modifications as mp
from ralph_portable import host_replica_stack as hrs
from ralph_portable.host_replication_executor import execute_replication_copy
from ralph_portable.replication_request import validate_replication_request


# --- the allowlist itself is safe -------------------------------------------
def test_allowlist_is_self_consistent():
    # No allowlisted key may ever collide with the denylist — guards a future bad edit.
    mp.assert_allowlist_is_self_consistent()
    for key in mp.CONFIG_ALLOWLIST:
        assert mp.forbidden_key_reason(key) is None


# --- allowlisted config deltas: accepted ------------------------------------
def test_allowlisted_config_delta_accepted():
    mods = {"config": {"AQ_WORKFLOW_SELECTOR": "explore", "AQ_MISSION_LABEL": "scale-x"}}
    assert mp.validate_modification_packet(mods) == []
    assert mp.allowlisted_config(mods) == {
        "AQ_WORKFLOW_SELECTOR": "explore", "AQ_MISSION_LABEL": "scale-x"}


def test_legacy_flat_config_still_works_when_allowlisted():
    mods = {"AQ_MISSION_CADENCE": "hourly", "AQ_MISSION_MAX_ATTEMPTS": 3}
    assert mp.validate_modification_packet(mods) == []
    assert mp.allowlisted_config(mods) == mods


def test_allowlisted_config_lands_in_compose_overlay():
    overlay = mp.render_config_overlay({"AQ_WORKFLOW_SELECTOR": "explore"})
    assert "AQ_WORKFLOW_SELECTOR: explore" in overlay
    assert "services:" in overlay and "app:" in overlay and "environment:" in overlay


# --- FORBIDDEN config deltas: rejected (the malicious-modification proofs) ---
# Each of these, if it slipped through, would let a replica weaken its OWN safety.
FORBIDDEN_CONFIG = {
    "auto_approve_own_replication": {"AQ_REPLICATION_AUTO_APPROVE": "1"},
    "raise_own_replica_cap": {"AQ_REPLICATION_MAX_REPLICAS": "999"},
    "lower_own_memory_gate": {"AQ_REPLICATION_MIN_FREE_MEM_PCT": "0"},
    "point_own_cap_lock_elsewhere": {"AQ_REPLICATION_LOCK_FILE": "/tmp/x"},
    "steal_db_owner_secret": {"AQ_DB_OWNER_PASSWORD": "pwned"},
    "steal_a_token": {"AQ_GOVERNANCE_DECISION_TOKEN": "pwned"},
    "grant_docker_sock": {"DOCKER_HOST": "unix:///var/run/docker.sock"},
    "mount_a_sock": {"AQ_SOCK_PATH": "/var/run/docker.sock"},
    "rebind_to_all_interfaces": {"AQ_MGMT_BIND": "0.0.0.0"},
    "change_published_port": {"AQ_UI_PORT": "80"},
    "forge_identity": {"AQ_INSTANCE_ID": "urn:uuid:forged"},
    "hijack_governance": {"AQ_GOVERNANCE_ADJUDICATOR": "self"},
}


@pytest.mark.parametrize("name,delta", list(FORBIDDEN_CONFIG.items()), ids=list(FORBIDDEN_CONFIG))
def test_forbidden_config_delta_rejected_by_policy(name, delta):
    errors = mp.validate_modification_packet({"config": delta})
    assert errors, f"{name} must be rejected"
    # and it never survives the final apply-point filter
    assert mp.allowlisted_config({"config": delta}) == {}


@pytest.mark.parametrize("name,delta", list(FORBIDDEN_CONFIG.items()), ids=list(FORBIDDEN_CONFIG))
def test_forbidden_config_delta_rejected_at_propose_endpoint(name, delta):
    # Propose-time = the real POST /api/replication/propose HTTP endpoint -> 400.
    fastapi = pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency")
    from fastapi.testclient import TestClient
    from management.api.app import app

    client = TestClient(app)
    r = client.post("/api/replication/propose", json={
        "mode": "copy_with_modifications", "mission_id": "m",
        "requester_instance_id": "aq-1", "modifications": {"config": delta}})
    assert r.status_code == 400, f"{name} must be rejected at propose (got {r.status_code})"


@pytest.mark.parametrize("name,delta", list(FORBIDDEN_CONFIG.items()), ids=list(FORBIDDEN_CONFIG))
def test_forbidden_config_delta_rejected_at_host_apply(name, delta, tmp_path):
    # Apply-time = the docker stand-up. It re-validates and REFUSES before any side effect.
    packet = {"mode": "copy_with_modifications", "mission_id": "m",
              "requester_instance_id": "aq-1", "modifications": {"config": delta}}
    calls = []

    def compose_runner(*a, **k):  # must NEVER run for a forbidden modification
        calls.append(1)
        return subprocess.CompletedProcess([], 0)

    rec = hrs.stand_up_replica_stack(
        packet, status="auto_approved", repo_root=tmp_path, git_sha="x",
        state_root=tmp_path / "st", env={}, docker=lambda *a, **k: "",
        mem_reader=lambda: 90.0, compose_runner=compose_runner, do_health_check=False,
        lock_path=tmp_path / "cap.lock")
    assert rec["status"] == "refused" and rec["gate"] == "modification_policy", rec
    assert calls == [], f"{name}: compose must not run for a forbidden modification"
    assert not (tmp_path / "st").exists(), "no replica files materialized when refused"


def test_forbidden_config_delta_rejected_by_fs_executor(tmp_path):
    src = tmp_path / "ws"
    src.mkdir()
    (src / "instance.yaml").write_text("mission_id: m\n")
    packet = {"mode": "copy_with_modifications", "mission_id": "m",
              "requester_instance_id": "aq-1",
              "modifications": {"config": {"AQ_REPLICATION_AUTO_APPROVE": "1"}}}
    rec = execute_replication_copy(
        packet, status="auto_approved", source_workspace=src, dest_root=tmp_path / "out")
    assert rec["ok"] is False and rec["status"] == "failed"
    assert "AQ_REPLICATION_AUTO_APPROVE" in rec["error"]


# --- host re-validates a TAMPERED stored packet (does not trust storage) -----
def test_host_apply_rejects_tampered_stored_packet(tmp_path):
    """A packet that bypassed propose (or was mutated after approval) is still refused host-side."""
    tampered = {"mode": "copy_with_modifications", "mission_id": "m",
                "requester_instance_id": "aq-1",
                # smuggled after the fact — a replica trying to auto-approve its own replication
                "modifications": {"config": {"AQ_REPLICATION_AUTO_APPROVE": "true"}}}
    ran = []
    rec = hrs.stand_up_replica_stack(
        tampered, status="auto_approved", repo_root=tmp_path, git_sha="x",
        state_root=tmp_path / "st", env={}, docker=lambda *a, **k: "",
        mem_reader=lambda: 90.0, do_health_check=False, lock_path=tmp_path / "cap.lock",
        compose_runner=lambda *a, **k: ran.append(1) or subprocess.CompletedProcess([], 0))
    assert rec["status"] == "refused" and rec["gate"] == "modification_policy"
    assert ran == []


def test_a_modified_replica_cannot_disable_its_own_gates():
    """The replication safety-gate env keys are NOT guest-settable by any channel."""
    gate_keys = [
        "AQ_REPLICATION_AUTO_APPROVE", "AQ_REPLICATION_MAX_REPLICAS",
        "AQ_REPLICATION_MIN_FREE_MEM_PCT", "AQ_REPLICATION_LOCK_FILE",
        "AQ_REPLICATION_DAEMON_ENABLE",
    ]
    for k in gate_keys:
        assert k not in mp.CONFIG_ALLOWLIST
        assert mp.forbidden_key_reason(k) is not None
        assert mp.validate_modification_packet({"config": {k: "x"}})
        assert mp.allowlisted_config({"config": {k: "x"}}) == {}


# --- CODE deltas: safe-prefix recognized, safety/host paths REJECTED ---------
def test_code_path_classifier():
    assert mp.classify_code_path("workflows/explore.py") == "allowed_prefix"
    assert mp.classify_code_path("plugins/my_tool.py") == "allowed_prefix"
    assert mp.classify_code_path("README.md") == "out_of_scope"
    for evil in [
        "ralph_portable/host_replica_stack.py",
        "ralph_portable/host_replication_executor.py",
        "ralph_portable/replication_request.py",
        "ralph_portable/replication_modifications.py",
        "ralph_portable/import_firewall.py",
        "docker-compose.yml",
        "container/Dockerfile",
        "scripts/compose-with-secrets.sh",
        "schema/999_grants.sql",
        "scripts/host_replication_broker.py",
        ".github/workflows/ci.yml",
        "workflows/../ralph_portable/host_replica_stack.py",  # traversal
    ]:
        assert mp.classify_code_path(evil) == "forbidden", evil


def test_code_delta_to_safety_file_rejected_as_security():
    errs = mp.validate_code_deltas([{"path": "ralph_portable/host_replica_stack.py"}])
    assert errs and "forbidden code path" in errs[0]


def test_code_delta_safe_prefix_recognized_but_deferred():
    # Step 2 is config-deltas-only: a safe-prefix code delta is NOT a security breach but is
    # deferred (not applied), never silently accepted as an unbounded code path.
    errs = mp.validate_code_deltas([{"path": "workflows/explore.py"}])
    assert errs and "deferred to Step 3" in errs[0]
    assert "forbidden" not in errs[0]


def test_code_delta_rejected_at_propose_and_apply(tmp_path):
    mods = {"code": [{"path": "ralph_portable/host_replica_stack.py"}]}
    packet = {"mode": "copy_with_modifications", "mission_id": "m",
              "requester_instance_id": "aq-1", "modifications": mods}
    assert mp.validate_modification_packet(mods)  # propose-time policy (what the endpoint runs)
    rec = hrs.stand_up_replica_stack(
        packet, status="auto_approved", repo_root=tmp_path, git_sha="x",
        state_root=tmp_path / "st", env={}, docker=lambda *a, **k: "",
        mem_reader=lambda: 90.0, do_health_check=False, lock_path=tmp_path / "cap.lock",
        compose_runner=lambda *a, **k: subprocess.CompletedProcess([], 0))
    assert rec["status"] == "refused" and rec["gate"] == "modification_policy"  # apply


# --- structural / shape guards ----------------------------------------------
def test_unknown_section_rejected():
    assert mp.validate_modification_packet({"config": {}, "danger": {"x": 1}})


def test_empty_modifications_rejected():
    assert mp.validate_modification_packet({})
    assert mp.validate_modification_packet({"config": {}})


def test_non_scalar_config_value_rejected():
    assert mp.validate_modification_packet({"config": {"AQ_MISSION_NOTE": {"nested": 1}}})
    assert mp.validate_modification_packet({"config": {"AQ_MISSION_NOTE": "x\ninjected"}})


# --- straight_copy path is unchanged (no regression) -------------------------
def test_straight_copy_unaffected_by_policy():
    packet = {"mode": "straight_copy", "mission_id": "m", "requester_instance_id": "aq-1"}
    assert validate_replication_request(packet) == []


def test_allowlisted_stand_up_applies_config_overlay(tmp_path):
    """Happy path: an allowlisted config delta is validated, written as a config overlay, and
    passed to compose (proving the delta reaches the running replica)."""
    packet = {"mode": "copy_with_modifications", "mission_id": "m",
              "requester_instance_id": "aq-1",
              "modifications": {"config": {"AQ_WORKFLOW_SELECTOR": "explore"}}}
    captured = {}

    def compose_runner(compose_args, *, cwd, env, timeout=None):
        captured["args"] = compose_args
        return subprocess.CompletedProcess(compose_args, 0, stdout="", stderr="")

    rec = hrs.stand_up_replica_stack(
        packet, status="auto_approved", repo_root=tmp_path, git_sha="cafe",
        state_root=tmp_path / "st", env={}, docker=lambda *a, **k: "",
        mem_reader=lambda: 90.0, compose_runner=compose_runner, do_health_check=False,
        lock_path=tmp_path / "cap.lock")
    assert rec["ok"] and rec["status"] == "executed"
    assert rec["applied_config"] == {"AQ_WORKFLOW_SELECTOR": "explore"}
    # the config overlay was generated and handed to compose
    from pathlib import Path
    overlay = Path(rec["state_dir"]) / "config.yml"
    assert overlay.is_file()
    assert "AQ_WORKFLOW_SELECTOR: explore" in overlay.read_text()
    assert str(overlay) in captured["args"]
