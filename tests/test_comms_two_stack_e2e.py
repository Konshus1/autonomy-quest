"""End-to-end TWO-STACK relay: separate stores/networks (#4834 comms Phase 2, design §10 Phase 2 / §11 layer 3).

``test_two_stack_relay_across_separate_stores`` — ALWAYS runs. A replica app (its OWN store) is
published to over the authenticated comms API; the host relay pulls that replica's REAL
``/api/agent-comms/outbox`` HTTP endpoint (scoped read credential) and copies the claims into a
DISTINCT parent journal store. It proves the two stores never share a DB and that copies cross the
boundary as untrusted claims with relay provenance — the portable, deterministic ground truth.

The heavy REAL-Docker version — which stands up an actual ``aq-replica-*`` stack and exercises the
comms wire (Phases 2/3/5) over real loopback HTTP against real replica Postgres rows — lives in
``tests/test_comms_two_stack_live_e2e.py`` (gated on ``AQ_COMMS_DOCKER_LIVE=1``). It REPLACED the
former hollow ``test_live_docker_two_stack`` stub in this file, which was all skip-gates with no
body and "passed" without ever standing up a stack.
"""

from __future__ import annotations

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
