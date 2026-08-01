"""Task #4407 — adversarial-review regressions (codex #45623) at the API layer.

- POST bodies must REJECT unknown fields (no laundering) -> 422.
- A mid-session store outage must degrade to a stable 503, not a 500 traceback.

Kept separate from the co-owned test_management_api.py.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api import app as appmod  # noqa: E402
from management.api.store import StoreUnavailable  # noqa: E402

client = TestClient(appmod.app)


def test_post_bodies_reject_unknown_fields() -> None:
    # extra="forbid" -> 422 (not silently accepted/ignored)
    assert client.post("/api/tasks", json={"title": "x", "bogus": 1}).status_code == 422
    assert client.post(
        "/api/agent-comms", json={"from_handle": "a", "text": "b", "sneaky": True}
    ).status_code == 422
    assert client.post(
        "/api/replication/propose",
        json={"mode": "straight_copy", "mission_id": "m", "requester_instance_id": "r", "x": 1},
    ).status_code == 422
    assert client.post(
        "/api/manager/merge-decision",
        json={"decision": "approve_merge", "manager_handle": "m", "cohort_id": "c",
              "rationale": "r", "evil": 1},
    ).status_code == 422


class _DownStore:
    """Every read/write raises as if the DB dropped mid-session."""

    backing = "postgres"

    def _boom(self, *a, **k):
        raise StoreUnavailable("connection reset")

    workstreams = tasks = comms = replications = merges = _boom
    create_task = create_comm = add_replication = add_merge = _boom


def test_store_outage_degrades_to_503_not_500(monkeypatch) -> None:
    monkeypatch.setattr(appmod, "store", _DownStore())
    for path in ("/api/tasks", "/api/agent-comms", "/api/workstreams", "/api/ralph/state"):
        r = client.get(path)
        assert r.status_code == 503, f"{path} -> {r.status_code} (want 503)"
        body = r.json()
        assert body["ok"] is False
        assert "unavailable" in body["error"].lower()
