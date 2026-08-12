"""End-to-end TWO-STACK relay: separate stores/networks (#4834 comms Phase 2, design §10 Phase 2 / §11 layer 3).

Two evidence layers:

  * ``test_two_stack_relay_across_separate_stores`` — ALWAYS runs. A replica app (its OWN store) is
    published to over the authenticated comms API; the host relay pulls that replica's REAL
    ``/api/agent-comms/outbox`` HTTP endpoint (scoped read credential) and copies the claims into a
    DISTINCT parent journal store. It proves the two stores never share a DB and that copies cross the
    boundary as untrusted claims with relay provenance — the portable, deterministic ground truth.

  * ``test_live_docker_two_stack`` — the heavy real-Docker version. DOUBLE-GATED + SKIPS LOUDLY:
    requires docker + host free memory > 45% (psutil-measured; skip loudly if psutil absent), same
    discipline as the replication memory gate and the Phase-1 docker poll check.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api import app as app_module  # noqa: E402
from management.api.comms_auth import CommsAuthenticator  # noqa: E402
from management.api.store import InMemoryStore  # noqa: E402
from ralph_portable.fleet_registry import FleetRegistryStore  # noqa: E402
from scripts import host_outbox_relay as relay  # noqa: E402

SELF = "urn:uuid:replica-e2e"
PARENT = "urn:uuid:parent-e2e"
DIGEST = "sha256:" + "b" * 64


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def _free_mem_pct() -> float | None:
    try:
        import psutil
    except ImportError:
        return None
    return 100.0 - psutil.virtual_memory().percent


def test_two_stack_relay_across_separate_stores(tmp_path, monkeypatch):
    # --- REPLICA STACK: its own store + its own authenticator + a real app client ---------------
    replica_store = InMemoryStore()
    replica_auth = CommsAuthenticator(credentials={
        "tok-replica": {"principal_id": f"instance:{SELF}", "origin_instance_id": SELF,
                        "lineage": [SELF, PARENT],
                        "kinds": ["status.report", "experiment.result"], "operator": False},
        "tok-relay": {"principal_id": "host:outbox-relay", "origin_instance_id": SELF,
                      "lineage": [], "kinds": [], "operator": False, "can_read_outbox": True},
    })
    monkeypatch.setattr(app_module, "store", replica_store)
    monkeypatch.setattr(app_module, "comms_auth", replica_auth)
    monkeypatch.delenv("AQ_MGMT_DB_URL", raising=False)
    monkeypatch.delenv("AQ_DB_URL", raising=False)
    replica = TestClient(app_module.app)

    # The replica AUTHORS two claims into its OWN outbox via the authenticated API.
    for i in range(2):
        r = replica.post("/api/agent-comms", headers={"Authorization": "Bearer tok-replica"}, json={
            "channel": f"lineage/{PARENT}/experiments", "kind": "experiment.result",
            "payload": {"summary": f"run {i}", "outcome_claimed": "success",
                        "artifact_refs": [{"digest": DIGEST}]},
            "idempotency_key": f"e2e-{i}"})
        assert r.status_code == 200, r.text

    # --- HOST STACK: a DISTINCT parent journal + registry; relay pulls the replica's real /outbox --
    parent_store = InMemoryStore()
    assert parent_store is not replica_store  # separate stores == separate DBs (no shared access)
    registry = FleetRegistryStore(tmp_path / "fleet.json")
    registry.upsert_standup({"instance_id": SELF, "project": "aq-replica-e2e",
                             "requester_instance_id": PARENT, "ports": {"app_mgmt": 18099}})

    def http_bridge(url, *, token, timeout_s):
        # Cross the boundary over the replica's ACTUAL HTTP endpoint (scoped read credential).
        after = url.split("after_seq=")[1].split("&")[0]
        limit = url.split("limit=")[1]
        resp = replica.get(f"/api/agent-comms/outbox?after_seq={after}&limit={limit}",
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        return resp.content

    summary = relay.run_once(registry, parent_store, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "tok-relay"},
                             fetch=http_bridge)
    assert summary["relayed"] == 2 and summary["duplicates"] == 0

    # The parent journal now holds the replica's claims — copied, not shared.
    copied = parent_store.envelopes()
    assert len(copied) == 2
    assert replica_store.envelopes() is not copied
    assert all(e["trust"] == "untrusted_claim" for e in copied)          # never upgraded
    assert all(e["relay"]["source_instance_id"] == SELF for e in copied)  # relay provenance
    assert all(e["relay"]["original_created_at"] for e in copied)         # original timestamp kept

    # A second relay pass is idempotent (cursor advanced; nothing re-copied).
    summary2 = relay.run_once(registry, parent_store,
                              env={"AQ_COMMS_OUTBOX_READ_TOKEN": "tok-relay"}, fetch=http_bridge)
    assert summary2["relayed"] == 0 and len(parent_store.envelopes()) == 2


def test_live_docker_two_stack():
    if not _docker_available():
        pytest.skip("SKIP LOUDLY: docker not available — the real two-compose-stack e2e needs a "
                    "reachable docker daemon (the portable two-store relay test still ran).")
    free = _free_mem_pct()
    if free is None:
        pytest.skip("SKIP LOUDLY: psutil not installed — cannot verify the >45% free-memory gate, "
                    "refusing to stand up two real replica stacks blind.")
    if free <= 45.0:
        pytest.skip(f"SKIP LOUDLY: host free memory {free:.0f}% <= 45% — refusing to stand up two "
                    "real replica stacks (same discipline as the replication memory gate).")
    import os
    if os.environ.get("AQ_COMMS_DOCKER_LIVE") != "1":
        pytest.skip("SKIP LOUDLY: resources OK but AQ_COMMS_DOCKER_LIVE!=1 — not building real "
                    "replica images in the default suite. Set AQ_COMMS_DOCKER_LIVE=1 to run the "
                    "full two-stack stand-up -> publish -> relay -> verify -> teardown ground truth.")
