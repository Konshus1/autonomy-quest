"""GET /api/fleet endpoint tests (#4834 comms Phase 1, design §9.2 + safety note N3).

Proves:
  * the fleet view is OPERATOR-CREDENTIALED (N3): unauthenticated -> 401, a bare instance
    credential -> 403, the operator credential -> 200 (not world-readable on the mgmt port);
  * it projects the host-authoritative topology from host_observed health.observed events in the
    parent journal (never the host registry — the endpoint imports no host module);
  * only trust=host_observed drives health; a replica's untrusted_claim of the same kind is ignored;
  * the latest observation per instance wins (live -> down transition surfaces);
  * message counts + redacted host-local port render.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api import app as app_module  # noqa: E402
from management.api.comms_auth import CommsAuthenticator  # noqa: E402
from management.api.comms_envelope import build_envelope  # noqa: E402
from management.api.store import InMemoryStore  # noqa: E402


OPERATOR_TOKEN = "op-token"
INSTANCE_TOKEN = "inst-token"
A = "urn:uuid:a"
PARENT = "urn:uuid:parent"


@pytest.fixture
def client(monkeypatch):
    store = InMemoryStore()
    monkeypatch.setattr(app_module, "store", store)
    auth = CommsAuthenticator(credentials={
        OPERATOR_TOKEN: {"principal_id": "operator:local", "origin_instance_id": "operator",
                         "lineage": [], "kinds": ["operator.message"], "operator": True},
        INSTANCE_TOKEN: {"principal_id": f"instance:{A}", "origin_instance_id": A,
                         "lineage": [A, PARENT], "kinds": ["status.report"], "operator": False},
    })
    monkeypatch.setattr(app_module, "comms_auth", auth)
    monkeypatch.delenv("AQ_MGMT_DB_URL", raising=False)
    monkeypatch.delenv("AQ_DB_URL", raising=False)
    return TestClient(app_module.app), store


def _host_observed(instance_id, *, state, port=5104, sha="cafe", project="aq-replica-a",
                   now_ms=None):
    """A host_observed health.observed envelope shaped exactly as the poller writes it."""
    return build_envelope(
        origin_instance_id=PARENT, principal_id="host:fleet-poller",
        channel=f"instance/{instance_id}/health", kind="health.observed",
        target_instance_id=instance_id, target_handle="replica", trust="host_observed",
        now_ms=now_ms,
        payload={
            "health_state": state, "reachable": state == "live", "observed_git_sha": sha,
            "reason": state, "cycling": True, "seconds_since_last_cycle": 10.0,
            "topology": {
                "instance_id": instance_id, "project": project,
                "parent_instance_id": PARENT, "lineage": [PARENT, instance_id],
                "created_at": "2026-01-01T00:00:00+00:00", "workflow": "flagship",
                "workflow_version": "v1", "expected_git_sha": sha, "app_mgmt_port": port,
                "lifecycle_state": "live", "counts_against_cap": True,
                "credential_present": True, "credential_revoked": False,
            },
        })


def _op():
    return {"Authorization": f"Bearer {OPERATOR_TOKEN}"}


# --- N3: operator-credentialed -----------------------------------------------
def test_fleet_unauthenticated_is_401(client):
    c, _ = client
    assert c.get("/api/fleet").status_code == 401


def test_fleet_bare_instance_credential_is_403(client):
    c, _ = client
    r = c.get("/api/fleet", headers={"Authorization": f"Bearer {INSTANCE_TOKEN}"})
    assert r.status_code == 403  # only the operator may read the whole fleet


def test_fleet_operator_credential_is_200(client):
    c, store = client
    store.create_envelope(_host_observed(A, state="live"))
    r = c.get("/api/fleet", headers=_op())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["count"] == 1
    item = body["items"][0]
    assert item["instance_id"] == A
    assert item["health_state"] == "live"
    assert item["project"] == "aq-replica-a"
    assert item["lineage"] == [PARENT, A]
    assert item["git_sha"] == "cafe"
    # Redacted host-local port — present as text, never a clickable URL.
    assert item["endpoint_redacted"] == "127.0.0.1:5104 (host-local)"
    assert "http://" not in item["endpoint_redacted"]
    assert item["counts_against_cap"] is True
    assert item["credential_present"] is True


# --- projection semantics ----------------------------------------------------
def test_latest_host_observation_wins(client):
    c, store = client
    store.create_envelope(_host_observed(A, state="live", now_ms=1_000))
    store.create_envelope(_host_observed(A, state="down", now_ms=2_000))  # later
    item = c.get("/api/fleet", headers=_op()).json()["items"][0]
    assert item["health_state"] == "down"  # latest wins
    assert item["health_observation_count"] == 2


def test_replica_untrusted_claim_is_ignored_for_health(client):
    c, store = client
    store.create_envelope(_host_observed(A, state="live"))
    # A replica publishes health.observed as an untrusted_claim — must NOT drive/overwrite health.
    replica_claim = build_envelope(
        origin_instance_id=A, principal_id=f"instance:{A}",
        channel=f"instance/{A}/health", kind="health.observed",
        target_instance_id=A, trust="untrusted_claim",
        payload={"health_state": "down", "topology": {"instance_id": A}})
    store.create_envelope(replica_claim)
    item = c.get("/api/fleet", headers=_op()).json()["items"][0]
    assert item["health_state"] == "live"  # the untrusted replica claim did not overwrite host truth
    assert item["trust"] == "host_observed"


def test_message_counts_include_other_envelopes(client):
    c, store = client
    store.create_envelope(_host_observed(A, state="live"))
    # A status.report on the instance channel counts toward message_count but not health.
    store.create_envelope(build_envelope(
        origin_instance_id=A, principal_id=f"instance:{A}", channel=f"instance/{A}/local",
        kind="status.report", target_instance_id=A, payload={"text": "hi"}))
    item = c.get("/api/fleet", headers=_op()).json()["items"][0]
    assert item["message_count"] == 2
    assert item["health_observation_count"] == 1


def test_empty_fleet_when_no_host_observations(client):
    c, _ = client
    body = c.get("/api/fleet", headers=_op()).json()
    assert body["ok"] and body["count"] == 0 and body["items"] == []
