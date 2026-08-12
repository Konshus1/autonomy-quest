"""Host-owned topology registry tests (#4834 comms Phase 0, design §5).

Proves: stand-up populates the registry with the full port map + lineage + credential fingerprint;
teardown marks torn_down and revokes the credential; and a host/broker restart reconciles truth from
Docker labels + each replica's replica.json (records live stacks, marks vanished ones torn_down).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ralph_portable import fleet_registry as fr
from ralph_portable import host_replica_stack as hrs


def _fake_docker(mapping):
    def run(args, *, check=True, timeout=None, env=None, cwd=None):
        return mapping.get(args[0], "")
    return run


def _valid_packet():
    return {"mode": "straight_copy", "mission_id": "m1", "requester_instance_id": "urn:uuid:parent"}


# --- unit: registry primitives ----------------------------------------------
def test_upsert_records_full_port_map_and_lineage(tmp_path):
    reg = fr.FleetRegistryStore(tmp_path / "reg.json")
    record = {
        "instance_id": "urn:uuid:a", "project": "aq-replica-a",
        "requester_instance_id": "urn:uuid:parent",
        "ports": {"postgres": 5001, "governance": 5002, "app_ui": 5003, "app_mgmt": 5004},
        "health_url": "http://127.0.0.1:5004/health", "git_sha": "cafe", "mission_id": "m1",
    }
    entry = reg.upsert_standup(record, lifecycle=fr.LIFECYCLE_LIVE, credential_secret="s3cr3t")
    assert entry["ports"] == {"postgres": 5001, "governance": 5002, "app_ui": 5003, "app_mgmt": 5004}
    assert entry["parent_instance_id"] == "urn:uuid:parent"
    assert entry["lifecycle_state"] == fr.LIFECYCLE_LIVE
    assert entry["credential_fingerprint"].startswith("sha256:")
    assert entry["credential_revoked"] is False
    # persisted + retrievable
    assert reg.get("urn:uuid:a")["git_sha"] == "cafe"


def test_teardown_marks_torn_down_and_revokes_credential(tmp_path):
    reg = fr.FleetRegistryStore(tmp_path / "reg.json")
    reg.upsert_standup(
        {"instance_id": "urn:uuid:a", "project": "aq-replica-a", "ports": {}},
        credential_secret="s")
    updated = reg.mark_torn_down_by_project("aq-replica-a")
    assert len(updated) == 1
    entry = reg.get("urn:uuid:a")
    assert entry["lifecycle_state"] == fr.LIFECYCLE_TORN_DOWN
    assert entry["credential_revoked"] is True
    assert reg.credential_revoked("urn:uuid:a") is True


def test_reconcile_rebuilds_from_docker_labels_and_replica_json(tmp_path):
    # Two replicas materialized their replica.json; one is still live, one has vanished from docker.
    state_root = tmp_path / "replicas"
    for proj, iid, sha, status in [
        ("aq-replica-live", "urn:uuid:live", "aaa", "executed"),
        ("aq-replica-gone", "urn:uuid:gone", "bbb", "executed"),
    ]:
        d = hrs.replica_dir(proj, state_root=state_root)
        d.mkdir(parents=True)
        (d / "replica.json").write_text(json.dumps({
            "instance_id": iid, "project": proj, "status": status, "git_sha": sha,
            "requester_instance_id": "urn:uuid:parent",
            "ports": {"postgres": 1, "governance": 2, "app_ui": 3, "app_mgmt": 4},
            "health_url": f"http://127.0.0.1:4/health",
        }))

    reg = fr.FleetRegistryStore(state_root / "fleet-registry.json")
    # First reconcile: both stacks present in docker.
    s1 = reg.reconcile(
        ["aq-replica-live", "aq-replica-gone"],
        replica_json_loader=lambda p: fr.load_replica_json(p, state_root=state_root))
    assert s1["added"] == 2
    assert reg.get("urn:uuid:live")["lifecycle_state"] == fr.LIFECYCLE_LIVE
    assert reg.get("urn:uuid:live")["ports"]["app_mgmt"] == 4

    # Second reconcile after 'gone' disappeared from docker labels -> torn_down + revoked.
    s2 = reg.reconcile(
        ["aq-replica-live"],
        replica_json_loader=lambda p: fr.load_replica_json(p, state_root=state_root))
    assert s2["torn_down"] == 1
    assert reg.get("urn:uuid:gone")["lifecycle_state"] == fr.LIFECYCLE_TORN_DOWN
    assert reg.get("urn:uuid:gone")["credential_revoked"] is True
    assert reg.get("urn:uuid:live")["lifecycle_state"] == fr.LIFECYCLE_LIVE


def test_reconcile_helper_uses_docker_enumeration(tmp_path):
    # host_replica_stack.reconcile_fleet_registry wires list_replica_projects + load_replica_json.
    state_root = tmp_path / "replicas"
    d = hrs.replica_dir("aq-replica-x", state_root=state_root)
    d.mkdir(parents=True)
    (d / "replica.json").write_text(json.dumps({
        "instance_id": "urn:uuid:x", "project": "aq-replica-x", "status": "executed",
        "ports": {"postgres": 1, "governance": 2, "app_ui": 3, "app_mgmt": 4}}))
    docker = _fake_docker({"ps": "aq-replica-x\n"})
    summary = hrs.reconcile_fleet_registry(state_root=state_root, docker=docker)
    assert summary["added"] == 1
    reg = fr.FleetRegistryStore(state_root / "fleet-registry.json")
    assert reg.get("urn:uuid:x")["lifecycle_state"] == fr.LIFECYCLE_LIVE


# --- integration: stand-up + teardown drive the registry --------------------
def test_stand_up_populates_registry_then_teardown_revokes(tmp_path):
    state_root = tmp_path / "replicas"

    def compose_runner(compose_args, *, cwd, env, timeout=None):
        return subprocess.CompletedProcess(compose_args, 0, stdout="", stderr="")

    rec = hrs.stand_up_replica_stack(
        _valid_packet(), status="auto_approved", repo_root=tmp_path, git_sha="beef",
        state_root=state_root, env={}, docker=_fake_docker({"ps": ""}),
        mem_reader=lambda: 90.0, compose_runner=compose_runner, do_health_check=False,
        lock_path=tmp_path / "cap.lock")
    assert rec["status"] == "executed"
    assert "registry_error" not in rec, rec.get("registry_error")

    reg = fr.FleetRegistryStore(state_root / "fleet-registry.json")
    entry = reg.get(rec["instance_id"])
    assert entry is not None
    assert entry["lifecycle_state"] == fr.LIFECYCLE_LIVE
    assert entry["parent_instance_id"] == "urn:uuid:parent"
    assert entry["ports"]["app_mgmt"] == rec["ports"]["app_mgmt"]
    assert entry["credential_fingerprint"] == rec["credential_fingerprint"]
    assert entry["credential_revoked"] is False

    # Teardown reaps + revokes the credential in the registry.
    hrs.teardown_replica(rec["project"], state_root=state_root, docker=_fake_docker({}))
    torn = reg.get(rec["instance_id"])
    assert torn["lifecycle_state"] == fr.LIFECYCLE_TORN_DOWN
    assert torn["credential_revoked"] is True


def test_secret_file_carries_a_distinct_comms_token(tmp_path):
    body = hrs.render_secret_file("urn:uuid:abc", comms_token="tok123",
                                  parent_instance_id="urn:uuid:parent")
    assert "AQ_COMMS_INSTANCE_TOKEN=tok123" in body
    assert "AQ_COMMS_PARENT_INSTANCE_ID=urn:uuid:parent" in body
    # and it is not one of the DB/governance secret keys (distinct credential material)
    assert "AQ_COMMS_INSTANCE_TOKEN" not in hrs._SECRET_KEYS
