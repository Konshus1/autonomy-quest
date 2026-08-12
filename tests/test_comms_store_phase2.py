"""Store-level Phase 2 unit tests (#4834 comms Phase 2): cursor, idempotent relay, verification.

Exercised on ``InMemoryStore`` (the default backing; the Pg path mirrors the same contract with
``ON CONFLICT`` for the id-idempotent relay and the parent-owned verification upsert).
"""

from __future__ import annotations

from management.api.store import InMemoryStore


def _env(i: int, *, idem=None, trust="untrusted_claim"):
    return {"id": f"id-{i}", "origin_instance_id": "urn:uuid:self",
            "principal_id": "instance:self", "channel": "lineage/p/experiments",
            "target": {"instance_id": "p", "handle": "parent"}, "kind": "experiment.result",
            "payload": {"summary": f"r{i}"}, "idempotency_key": idem,
            "created_at": "2026-08-12T00:00:00+00:00", "expires_at": None,
            "trust": trust, "delivery": "accepted"}


def test_envelopes_after_is_a_monotonic_cursor():
    s = InMemoryStore()
    for i in range(3):
        s.create_envelope(_env(i, idem=f"k{i}"))
    page = s.envelopes_after(after_seq=0, limit=2)
    assert [p["seq"] for p in page] == [1, 2]
    page2 = s.envelopes_after(after_seq=2)
    assert [p["seq"] for p in page2] == [3]
    assert s.envelopes_after(after_seq=3) == []


def test_relay_envelope_idempotent_on_global_id():
    s = InMemoryStore()
    r1 = s.relay_envelope(_env(1))
    r2 = s.relay_envelope(_env(1))  # same global id
    assert r1["duplicate"] is False and r2["duplicate"] is True
    assert len(s.envelopes()) == 1


def test_relay_envelope_distinct_from_create_idempotency():
    # relay dedups on the global id even when idempotency_key is absent (create would just append).
    s = InMemoryStore()
    s.relay_envelope(_env(1, idem=None))
    s.relay_envelope(_env(1, idem=None))
    assert len(s.envelopes()) == 1


def test_verification_is_separate_from_the_claim():
    s = InMemoryStore()
    s.create_envelope(_env(1, idem="k1"))
    assert s.verifications() == {}  # default: nothing verified
    rec = s.set_verification("id-1", state="verified", verifier="operator:local",
                             reason="checked digest against real run")
    assert rec["state"] == "verified"
    assert s.verifications()["id-1"]["verifier"] == "operator:local"
    # Re-setting overwrites the parent-owned state; the claim envelope is never touched.
    s.set_verification("id-1", state="rejected", verifier="operator:local")
    assert s.verifications()["id-1"]["state"] == "rejected"
    assert s.envelopes()[0]["trust"] == "untrusted_claim"
