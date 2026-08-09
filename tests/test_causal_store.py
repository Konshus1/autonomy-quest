"""Task #4407 slice 1c — durable (Postgres) causal-edge store; identity-keyed, upsert-preserving."""

from __future__ import annotations

import os

import pytest

from management.api.causal_store import build_causal_store
from ralph_portable.causal_edge_store import InMemoryCausalEdgeStore
from ralph_portable.causal_edges import edge_identity, surprise


def _edge(**over):
    e = {
        "cause": "send_followup", "effect": "email_sent", "scope": {"region": "us"},
        "formality": "evidential", "strictness": "advisory", "directness": "predicate",
        "executor": {"kind": "predicate", "ref": "checks/email_sent.sql"},
        "predicted_certainty": 0.7,
    }
    e.update(over)
    return e


def test_build_defaults_in_memory_without_db():
    s = build_causal_store(env={"PATH": "/usr/bin"})
    assert isinstance(s, InMemoryCausalEdgeStore)


def test_configured_governed_store_fails_closed_on_dead_dsn():
    # Once durable governance is configured, an outage must not silently restore the
    # ungoverned in-memory planner and provisional authority.
    with pytest.raises(Exception):
        build_causal_store(env={"AQ_MGMT_DB_URL": "postgresql://nobody@127.0.0.1:1/none"})


@pytest.mark.skipif(
    not os.environ.get("AQ_TEST_DB_URL"),
    reason="set AQ_TEST_DB_URL to a reachable Postgres to run the durable causal-store test",
)
def test_pg_causal_store_durable_roundtrip_and_upsert() -> None:
    from management.api.causal_store import PgCausalEdgeStore

    url = os.environ["AQ_TEST_DB_URL"]
    s = PgCausalEdgeStore(url)
    # unique identity per run so repeated runs don't collide
    tag = f"slice1c-{os.getpid()}"
    ident = s.put(_edge(cause=tag, effect="e1"))
    assert ident == edge_identity(_edge(cause=tag, effect="e1"))
    assert s.get(ident)["cause"] == tag

    # accumulate support via evidence, then confirm it PERSISTS across a fresh store instance
    for _ in range(5):
        last = s.record_evidence(ident, surprise(0.7, True))
    assert last["action"] == "promote" and last["gated"] is True   # earned + gated
    s2 = PgCausalEdgeStore(url)                                     # NEW instance = durable read
    assert s2.get(ident)["support_count"] == 5
    assert len(s2.get(ident)["evidence"]) == 5
    assert s2.get(ident)["formality"] == "evidential"              # NOT auto-promoted

    # a promotion updates the dials at the SAME identity; support/evidence carry over
    s.put(_edge(cause=tag, effect="e1", formality="formal", directness="script",
                executor={"kind": "script", "ref": "scripts/send.py"}))
    e = PgCausalEdgeStore(url).get(ident)
    assert e["formality"] == "formal" and e["support_count"] == 5

    # planning check reads the durable model
    prof = s.assess_plan([{"action": tag, "effect": "e1"}])
    assert prof["covered"] == 1 and prof["per_step"][0]["guaranteed"] is True

    # cleanup this run's row
    with s._connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM ralph_causal_edges WHERE cause=%s", (tag,))
