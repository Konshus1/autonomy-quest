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


def _forge_result_body(seq, *, kind="experiment.result", trust="untrusted_claim", **overrides):
    """An experiment.result-shaped outbox body (an ALLOWED kind) with attacker-chosen fields."""
    env = {
        "id": f"01RES{seq:020d}",
        "envelope_version": 1,
        "origin_instance_id": "urn:uuid:spoofed",
        "principal_id": "instance:urn:uuid:spoofed",
        "channel": f"lineage/{PARENT}/experiments",
        "target": {"instance_id": PARENT, "handle": "parent"},
        "kind": kind,
        "payload": {"summary": f"r{seq}", "outcome_claimed": "success"},
        "correlation_id": None, "in_reply_to": None, "idempotency_key": None,
        "created_at": "2026-08-12T00:00:00+00:00", "expires_at": None,
        "trust": trust, "delivery": "accepted",
    }
    env.update(overrides)
    return env


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
    # NON-VACUOUS: feed a body that FORGES trust=host_observed; the relay must force it back down.
    e1 = _replica_envelope(1)
    e1["trust"] = "host_observed"
    page = [{"seq": 1, "envelope": e1},
            {"seq": 2, "envelope": _replica_envelope(2)}]
    res = relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"},
                         fetch=_fake_fetch({0: page}))
    assert res["relayed"] == 2 and res["duplicates"] == 0
    copied = parent.envelopes()
    assert len(copied) == 2
    # Trust is FORCED to untrusted_claim (a relay can NEVER carry a replica-supplied host_observed)...
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


# --- RELAY-PATH TRUST/IDENTITY SANITIZATION (de-correlated safety review, blocking) ----------
# The adversary speaks to the RELAY, which reads the replica-controlled /outbox body. These prove the
# relay treats that body as FULLY UNTRUSTED INPUT — re-forcing trust, re-deriving identity, and
# re-validating the payload — so a replica can never mint host_observed, spoof a principal, or smuggle
# a non-digest artifact ref into the parent journal via the relay.

VICTIM = "urn:uuid:victim"


def _forged_outbox_item(seq, **overrides):
    """An arbitrary envelope body a MALICIOUS replica could return from its own /outbox."""
    env = {
        "id": f"01FORGE{seq:018d}",
        "envelope_version": 1,
        "origin_instance_id": "urn:uuid:spoofed",
        "principal_id": "operator:local",           # spoof: author-as-operator
        "channel": f"instance/{VICTIM}/health",
        "target": {"instance_id": VICTIM, "handle": "replica"},
        "kind": "health.observed",                  # spoof: a host-only kind
        "payload": {"health_state": "PWNED-healthy",
                    "topology": {"instance_id": VICTIM, "app_mgmt_port": 19999,
                                 "lifecycle_state": "live", "lineage": [VICTIM]}},
        "correlation_id": None, "in_reply_to": None, "idempotency_key": None,
        "created_at": "2026-08-12T00:00:00+00:00", "expires_at": None,
        "trust": "host_observed",                   # spoof: forge ground-truth trust
        "delivery": "accepted",
    }
    env.update(overrides)
    return env


def test_relay_forces_untrusted_and_drops_forged_host_observed(tmp_path):
    """RED on a064b28: the relay copied trust=host_observed verbatim, so GET /api/fleet served a
    forged victim row. Post-fix: a health.observed from a replica body is DROPPED (not an emit kind)
    and no host_observed lands, so the fleet view shows NO victim."""
    from management.api.fleet_view import fleet_from_envelopes

    reg = _registry(tmp_path)
    parent = InMemoryStore()
    page = [{"seq": 1, "envelope": _forged_outbox_item(1)}]
    res = relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"},
                         fetch=_fake_fetch({0: page}))
    # The forged health.observed never reaches the journal as host_observed ...
    assert all(e["trust"] == "untrusted_claim" for e in parent.envelopes())
    # ... and the fleet/topology view (host_observed only) shows NO victim row — no authority moved.
    assert fleet_from_envelopes(parent.envelopes()) == []
    assert res["rejected"] == 1 and res["relayed"] == 0
    # The cursor still advances past the rejected item so it never wedges the relay.
    assert reg.relay_cursor(SELF) == 1


def test_relay_forced_untrusted_on_an_allowed_kind_carrying_forged_trust(tmp_path):
    """A result IS an allowed kind, but a forged trust=host_observed in its body must still be forced
    down to untrusted_claim (RED on a064b28, which copied trust verbatim)."""
    reg = _registry(tmp_path)
    parent = InMemoryStore()
    forged = _forge_result_body(1, trust="host_observed")
    res = relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"},
                         fetch=_fake_fetch({0: [{"seq": 1, "envelope": forged}]}))
    assert res["relayed"] == 1
    assert parent.envelopes()[0]["trust"] == "untrusted_claim"


def test_relay_re_derives_spoofed_identity(tmp_path):
    """A replica body claiming principal_id=operator:local + a spoofed origin must be re-derived to
    the registry instance the host is actually polling (RED on a064b28, which copied identity verbatim)."""
    reg = _registry(tmp_path)
    parent = InMemoryStore()
    forged = _forge_result_body(1, principal_id="operator:local", origin_instance_id="urn:uuid:spoofed")
    relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"},
                   fetch=_fake_fetch({0: [{"seq": 1, "envelope": forged}]}))
    e = parent.envelopes()[0]
    assert e["origin_instance_id"] == SELF                 # the polled instance, not the body's claim
    assert e["principal_id"] == f"instance:{SELF}"         # never author-as operator:local


def test_relay_drops_result_with_non_digest_artifact_ref(tmp_path):
    """A relayed experiment.result carrying a url/path artifact ref (no digest) must be DROPPED —
    the 'artifact refs are digests ONLY' invariant holds on the relay path too (RED on a064b28,
    which copied payload verbatim)."""
    reg = _registry(tmp_path)
    parent = InMemoryStore()
    forged = _forge_result_body(1)
    forged["payload"] = {"summary": "x", "outcome_claimed": "success",
                         "artifact_refs": [{"url": "http://169.254.169.254/latest/meta-data"}]}
    res = relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"},
                         fetch=_fake_fetch({0: [{"seq": 1, "envelope": forged}]}))
    assert res["rejected"] == 1 and res["relayed"] == 0
    assert parent.envelopes() == []                        # never reaches the journal
    # No relayed envelope anywhere carries a url artifact ref.
    for e in parent.envelopes():
        for ref in (e.get("payload") or {}).get("artifact_refs") or []:
            assert "url" not in ref and "path" not in ref


def test_relay_drops_non_emit_kind_from_replica_body(tmp_path):
    """A replica outbox may only carry status.report / experiment.progress / experiment.result. A
    work.request / operator.message / health.observed in the body is rejected, never relayed."""
    reg = _registry(tmp_path)
    parent = InMemoryStore()
    items = [{"seq": 1, "envelope": _forge_result_body(1, kind="operator.message")},
             {"seq": 2, "envelope": _forge_result_body(2, kind="work.request")}]
    res = relay.run_once(reg, parent, env={"AQ_COMMS_OUTBOX_READ_TOKEN": "t"},
                         fetch=_fake_fetch({0: items}))
    assert res["rejected"] == 2 and res["relayed"] == 0 and parent.envelopes() == []
