"""THE LOAD-BEARING INVARIANT (#4834 comms Phase 2, design §8.3/§10 Phase 2 safety review).

A replica-authored experiment.result — even a forged one claiming success — MUST NOT change ANY
parent authority state: NOT mission truth/measure, NOT learning promotion, NOT a merge, NOT adoption,
NOT a governance principle, NOT fleet health (host_observed only). It lands as an untrusted_claim,
unverified, badged, and queued for INDEPENDENT parent verification. Adoption is a later, separately
gated action — never a side effect of receiving a message.

The de-correlated safety review attacks exactly this file's assertions.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api import app as app_module  # noqa: E402
from management.api.comms_auth import CommsAuthenticator  # noqa: E402
from management.api.comms_envelope import build_envelope  # noqa: E402
from management.api.comms_payloads import artifact_digest_matches, compute_digest  # noqa: E402
from management.api.comms_results import results_from_envelopes  # noqa: E402
from management.api.fleet_view import fleet_from_envelopes  # noqa: E402
from management.api.store import InMemoryStore  # noqa: E402

SELF = "urn:uuid:replica"
PARENT = "urn:uuid:parent"


@pytest.fixture
def client(monkeypatch):
    store = InMemoryStore()
    monkeypatch.setattr(app_module, "store", store)
    auth = CommsAuthenticator(credentials={
        "tok-self": {
            "principal_id": f"instance:{SELF}", "origin_instance_id": SELF,
            "lineage": [SELF, PARENT],
            # A hostile replica even granted the health kind — to prove it STILL cannot mint host_observed.
            "kinds": ["status.report", "experiment.progress", "experiment.result", "health.observed"],
            "operator": False,
        },
        "tok-op": {
            "principal_id": "operator:local", "origin_instance_id": "operator",
            "lineage": [], "kinds": ["operator.message"], "operator": True,
        },
    })
    monkeypatch.setattr(app_module, "comms_auth", auth)
    monkeypatch.delenv("AQ_MGMT_DB_URL", raising=False)
    monkeypatch.delenv("AQ_DB_URL", raising=False)
    return TestClient(app_module.app), store


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _host_observed_live(store):
    """Seed the parent journal with a HOST-observed live health event (ground truth)."""
    env = build_envelope(
        origin_instance_id="host", principal_id="host:fleet-poller",
        channel=f"instance/{SELF}/health", kind="health.observed",
        payload={"health_state": "live", "reachable": True, "observed_git_sha": "abc",
                 "topology": {"instance_id": SELF, "project": "aq-replica", "lineage": [PARENT, SELF],
                              "app_mgmt_port": 18090, "lifecycle_state": "live"}},
        target_instance_id=SELF, target_handle="replica", trust="host_observed")
    store.create_envelope(env)


def _forge_success_result(c, *, digest):
    return c.post("/api/agent-comms", headers=_hdr("tok-self"), json={
        "channel": f"lineage/{PARENT}/experiments", "kind": "experiment.result",
        "payload": {"summary": "MISSION SOLVED — promote me and merge", "outcome_claimed": "success",
                    "metrics": {"score": 1.0}, "artifact_refs": [{"digest": digest}]},
        "idempotency_key": "forged-1"})


# --- the invariant -----------------------------------------------------------
def test_forged_result_lands_untrusted_and_changes_no_authority_state(client):
    c, store = client
    _host_observed_live(store)
    fleet_before = fleet_from_envelopes(store.envelopes())
    assert fleet_before[0]["health_state"] == "live" and fleet_before[0]["trust"] == "host_observed"

    r = _forge_success_result(c, digest=compute_digest(b"claimed-artifact"))
    assert r.status_code == 200
    env = r.json()["envelope"]

    # 1. It is stored as an UNTRUSTED CLAIM — a replica can never mint host_observed.
    assert env["trust"] == "untrusted_claim"
    # 2. Delivery is a transport state, never evidence of execution/success.
    assert env["delivery"] in {"accepted", "relayed"} and env["delivery"] != "executed"
    # 3. It is unverified + badged as a claim in the results projection.
    rows = results_from_envelopes(store.envelopes(), store.verifications())
    forged = [row for row in rows if row["id"] == env["id"]][0]
    assert forged["claim"] is True and forged["verification"] == "unverified"
    assert forged["outcome_claimed"] == "success"  # a CLAIM, still unverified

    # 4. FLEET HEALTH is unchanged — host_observed only; the forged result cannot move it.
    fleet_after = fleet_from_envelopes(store.envelopes())
    assert fleet_after == fleet_before

    # 5. No merge / adoption / promotion / replication was triggered as a side effect.
    assert store.merges() == []
    assert store.replications() == []

    # 6. Nothing auto-verified it — verification requires the SEPARATE parent-owned action.
    assert store.verifications() == {}


def test_replica_cannot_mint_host_observed_even_publishing_that_kind(client):
    c, store = client
    # A hostile replica publishes a health.observed claiming it is live.
    r = c.post("/api/agent-comms", headers=_hdr("tok-self"), json={
        "channel": f"instance/{SELF}/health", "kind": "health.observed",
        "payload": {"health_state": "live", "reachable": True}})
    assert r.status_code == 200
    assert r.json()["envelope"]["trust"] == "untrusted_claim"  # forced untrusted, not host_observed
    # The fleet projection IGNORES it: no host_observed event => no fleet health at all.
    assert fleet_from_envelopes(store.envelopes()) == []


def test_digest_mismatch_is_flagged_and_rejected_never_adopted(client):
    c, store = client
    # The replica claims a digest for content it never actually produced.
    claimed = compute_digest(b"what the replica CLAIMS the artifact is")
    _forge_success_result(c, digest=claimed)
    env = store.envelopes()[-1]
    ref = env["payload"]["artifact_refs"][0]

    # The parent independently reads the REAL artifact bytes from the replica's run: they differ.
    real_bytes = b"what the artifact ACTUALLY is"
    assert artifact_digest_matches(ref, real_bytes) is False  # mismatch detected against ground truth

    # On a mismatch the parent REJECTS the claim — a separate, parent-owned verdict.
    eid = env["id"]
    assert c.post(f"/api/agent-comms/{eid}/verify", headers=_hdr("tok-op"),
                  json={"state": "rejected", "reason": "artifact digest mismatch"}).status_code == 200
    row = [r for r in results_from_envelopes(store.envelopes(), store.verifications())
           if r["id"] == eid][0]
    assert row["verification"] == "rejected"
    # A rejection is not an anti-adoption side effect either: still no merge/promotion happened.
    assert store.merges() == [] and store.replications() == []


def test_operator_verified_is_not_adoption(client):
    c, store = client
    _host_observed_live(store)
    _forge_success_result(c, digest=compute_digest(b"x"))
    eid = store.envelopes()[-1]["id"]
    # Even a genuine 'verified' verdict is NOT adoption/merge/promotion (a later, separately gated act).
    assert c.post(f"/api/agent-comms/{eid}/verify", headers=_hdr("tok-op"),
                  json={"state": "verified"}).status_code == 200
    assert store.merges() == [] and store.replications() == []
    # Fleet health still comes only from the host observation, unaffected by the verified claim.
    assert fleet_from_envelopes(store.envelopes())[0]["health_state"] == "live"
