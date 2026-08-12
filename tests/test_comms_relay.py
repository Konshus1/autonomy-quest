"""Host outbox relay tests (#4834 comms Phase 2, design §4.3/§10 Phase 2).

Proves the relay copies a replica's authored claims into the PARENT journal:
  * at-least-once + duplicate suppression (idempotent on the global id),
  * per-replica cursor advance + restart resume,
  * SSRF guards (refuse redirects, reject non-loopback / non-outbox URLs),
  * teardown mid-transfer leaves no orphan / re-relay is safe,
  * copies keep trust=untrusted_claim and gain BOTH original + relay timestamps,
  * two SEPARATE stores — the relay copies across a boundary, it does not share a DB.
"""

from __future__ import annotations

import json

import pytest

from management.api.store import InMemoryStore
from ralph_portable.fleet_registry import FleetRegistryStore
from scripts import host_outbox_relay as relay


SELF = "urn:uuid:self"
PARENT = "urn:uuid:parent"


def _replica_envelope(i: int, *, kind="experiment.result"):
    """A replica-authored, untrusted claim as it would sit in the replica's OWN outbox."""
    return {
        "id": f"01ENV{i:020d}",
        "envelope_version": 1,
        "origin_instance_id": SELF,
        "principal_id": f"instance:{SELF}",
        "channel": f"lineage/{PARENT}/experiments",
        "target": {"instance_id": PARENT, "handle": "parent"},
        "kind": kind,
        "payload": {"summary": f"result {i}", "outcome_claimed": "success"},
        "correlation_id": None, "in_reply_to": None,
        "idempotency_key": f"idem-{i}",
        "created_at": "2026-08-12T00:00:00+00:00",
        "expires_at": None,
        "trust": "untrusted_claim",
        "delivery": "accepted",
    }


def _registry(tmp_path):
    reg = FleetRegistryStore(tmp_path / "fleet.json")
    reg.upsert_standup({
        "instance_id": SELF, "project": "aq-replica-x", "requester_instance_id": PARENT,
        "ports": {"app_mgmt": 18090}, "git_sha": "abc123",
    })
    return reg


def _fake_fetch(pages: dict[int, list[dict]]):
    """A fake outbox endpoint: maps after_seq -> the items page returned for that cursor."""
    def fetch(url, *, token, timeout_s):
        after = int(url.split("after_seq=")[1].split("&")[0])
        items = pages.get(after, [])
        return json.dumps({"ok": True, "items": items, "cursor": (items[-1]["seq"] if items else after),
                           "count": len(items)}).encode()
    return fetch


# --- happy path: copy to a SEPARATE parent journal ---------------------------
def test_relay_copies_claims_into_separate_parent_journal(tmp_path):
    reg = _registry(tmp_path)
    parent = InMemoryStore()  # the PARENT journal — a distinct store from the replica's outbox
    page = [{"seq": 1, "envelope": _replica_envelope(1)},
            {"seq": 2, "envelope": _replica_envelope(2)}]
    res = relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"},
                         fetch=_fake_fetch({0: page}))
    assert res["relayed"] == 2 and res["duplicates"] == 0
    copied = parent.envelopes()
    assert len(copied) == 2
    # Trust is preserved as untrusted_claim (a relay can NEVER upgrade to host_observed)...
    assert all(e["trust"] == "untrusted_claim" for e in copied)
    # ...and each copy carries BOTH the original and the relay timestamp + observer.
    assert copied[0]["relay"]["original_created_at"] == "2026-08-12T00:00:00+00:00"
    assert copied[0]["relay"]["relayed_by"] == "host:outbox-relay"
    assert copied[0]["delivery"] == "relayed"
    assert reg.relay_cursor(SELF) == 2


# --- at-least-once + duplicate suppression -----------------------------------
def test_re_relay_same_envelopes_is_idempotent(tmp_path):
    parent = InMemoryStore()  # ONE parent journal across two relay attempts
    page = [{"seq": 1, "envelope": _replica_envelope(1)}]
    # First pass copies it.
    reg1 = _registry(tmp_path / "a")
    relay.run_once(reg1, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"}, fetch=_fake_fetch({0: page}))
    # Crash BEFORE the cursor was persisted: a fresh relay (cursor 0) re-pulls the SAME page.
    reg2 = _registry(tmp_path / "b")
    res2 = relay.run_once(reg2, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"},
                          fetch=_fake_fetch({0: page}))
    # Idempotent on the global id: the re-pull is suppressed as a duplicate, no double effect.
    assert res2["duplicates"] == 1 and res2["relayed"] == 0
    assert len(parent.envelopes()) == 1


def test_cursor_restart_resumes_without_re_pull(tmp_path):
    reg = _registry(tmp_path)
    parent = InMemoryStore()
    p1 = [{"seq": 1, "envelope": _replica_envelope(1)}]
    relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"}, fetch=_fake_fetch({0: p1}))
    assert reg.relay_cursor(SELF) == 1
    # A fresh pass pulls ONLY past the watermark; only seq>1 is served for after_seq=1.
    p2 = [{"seq": 2, "envelope": _replica_envelope(2)}]
    res = relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"},
                         fetch=_fake_fetch({1: p2}))
    assert res["relayed"] == 1 and reg.relay_cursor(SELF) == 2
    assert [e["id"] for e in parent.envelopes()] == ["01ENV" + "0" * 19 + "1",
                                                     "01ENV" + "0" * 19 + "2"]


def test_relay_monotonic_cursor_never_rewinds(tmp_path):
    reg = _registry(tmp_path)
    reg.record_relay_cursor(SELF, cursor=5, relayed_at="a")
    reg.record_relay_cursor(SELF, cursor=3, relayed_at="b")  # a stale/racing batch
    assert reg.relay_cursor(SELF) == 5


# --- teardown mid-transfer: no orphan ----------------------------------------
def test_teardown_mid_transfer_no_orphan(tmp_path):
    reg = _registry(tmp_path)
    parent = InMemoryStore()
    # Partial batch relayed, then the replica is torn down before the rest.
    p = [{"seq": 1, "envelope": _replica_envelope(1)}]
    relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"}, fetch=_fake_fetch({0: p}))
    reg.mark_torn_down(SELF)
    # A torn-down replica is not live => the next relay pass skips it entirely (no orphan pull).
    res = relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"},
                         fetch=_fake_fetch({1: [{"seq": 2, "envelope": _replica_envelope(2)}]}))
    assert res["instances"] == 0  # nothing live to relay
    assert len(parent.envelopes()) == 1  # the already-copied claim persists; nothing partial/lost


# --- no token => fail-closed skip --------------------------------------------
def test_no_token_skips_fail_closed(tmp_path):
    reg = _registry(tmp_path)
    parent = InMemoryStore()
    res = relay.run_once(reg, parent, env={}, fetch=_fake_fetch({0: [
        {"seq": 1, "envelope": _replica_envelope(1)}]}))
    assert res["skipped"] == 1 and res["relayed"] == 0 and parent.envelopes() == []


# --- SSRF guards -------------------------------------------------------------
@pytest.mark.parametrize("bad_url", [
    "http://169.254.169.254/api/agent-comms/outbox",      # cloud metadata host
    "http://127.0.0.1:18090/api/replication/actuate",     # off the read-only outbox route
    "https://127.0.0.1:18090/api/agent-comms/outbox",     # non-http
    "http://evil.example/api/agent-comms/outbox",         # non-loopback
])
def test_assert_loopback_outbox_url_rejects(bad_url):
    with pytest.raises(ValueError):
        relay._assert_loopback_outbox_url(bad_url)


# --- dropped / expired -------------------------------------------------------
def test_expired_claim_is_dropped_not_relayed(tmp_path):
    reg = _registry(tmp_path)
    parent = InMemoryStore()
    expired = _replica_envelope(1)
    expired["expires_at"] = "2000-01-01T00:00:00+00:00"  # long past
    live = _replica_envelope(2)
    page = [{"seq": 1, "envelope": expired}, {"seq": 2, "envelope": live}]
    res = relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"},
                         fetch=_fake_fetch({0: page}))
    # The expired one is DROPPED (never copied); the live one is relayed; the cursor advances past both.
    assert res["dropped"] == 1 and res["relayed"] == 1
    assert [e["payload"]["summary"] for e in parent.envelopes()] == ["result 2"]
    assert reg.relay_cursor(SELF) == 2  # dropped message does not wedge the cursor


def test_no_redirect_handler_refuses_to_follow():
    # A replica returning a 3xx must NOT be followed (would be an arbitrary-GET SSRF primitive).
    handler = relay._NoRedirectHandler()
    assert handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:9/evil") is None
