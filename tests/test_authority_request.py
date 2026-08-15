"""Slice 3 — authority-change REQUEST (actor proposes to open a gate, never self-grants).

RED-FIRST properties this locks:
  * flag OFF (default) => the loop emits NOTHING and touches no table (byte-identical to Slice 2).
  * flag ON + a categorically-GATED work => exactly ONE pending request per gated class, and it
    DEDUPES across repeated cycles (one open request per (instance, class, action-digest)).
  * emitting the request does NOT change the gate decision — the work still parks (the gate reason is
    identical whether or not the request flag is set).
  * the actor cannot mark its own request approved: the request is born PENDING / not-authorized, and
    disposition is a SEPARATE operator surface (grant-backed boundary is proven in the PG test).

Pure + fake-db level here; the DB grant/append-only boundary is proven against the real schema in
test_authority_request_pg.py.
"""
from __future__ import annotations

import types
from decimal import Decimal

import pytest

from runner.authority_envelope import (
    CLASS_CONTACT_HUMAN, CLASS_SPEND_MONEY, DEFAULT_ENVELOPE, from_config,
    assess_plan_envelope,
)
from runner.authority_request import (
    STATUS_PENDING, REASON_GATED_CLASS, AuthorityChangeRequest, action_digest,
    build_requests_from_verdict, enabled,
)
from runner.loop import Loop


# -- fixtures / fakes ---------------------------------------------------------------------------
def _inst(envelope=DEFAULT_ENVELOPE, must_ask=(), gate_level=3, level="act-external", approval=3.0,
          resident_agent="codex"):
    autonomy = types.SimpleNamespace(level=level, blast_radius_gate_level=gate_level,
                                     per_plan_approval_usd=approval)
    return types.SimpleNamespace(
        budget=types.SimpleNamespace(autonomy=autonomy),
        mission=types.SimpleNamespace(boundaries=types.SimpleNamespace(must_ask_first=list(must_ask))),
        engine=types.SimpleNamespace(resident_agent=resident_agent),
        authority_envelope=envelope)


def _work(action, wid=1, blast_level=0, spends_money=False, touches_human=False, commits=False,
          expense="0", reversible=True, requires_human=False, rationale="do the thing"):
    return types.SimpleNamespace(
        id=wid, requires_human=requires_human, reversible=reversible,
        expected_expense_usd=Decimal(expense), blast_radius_level=blast_level,
        spends_money=spends_money, touches_human=touches_human, commits=commits, rationale=rationale,
        plan={"steps": [{"step_id": "s1", "action": action, "blast_radius":
                         {"affected_entities_upper_bound": 1, "public_or_unbounded": False,
                          "production_wide": False, "irreversible_external_write": False}}]})


class FakeDb:
    """Models the append-only + idempotent request store: emit dedupes on the UNIQUE key, and a
    disposition is a SEPARATE record. There is deliberately NO actor-callable 'approve' here."""
    def __init__(self):
        self.requests = []          # every accepted request row
        self._seen = set()          # UNIQUE (instance_id, capability_class, proposed_action_digest)
        self.dispositions = {}      # request_id -> (disposition, disposed_by)
        self.parked = []            # (work_id, note)
        self.notified = []

    def emit_authority_change_request(self, req) -> bool:
        key = (req.instance_id, req.capability_class, req.proposed_action_digest)
        if key in self._seen:
            return False            # ON CONFLICT DO NOTHING
        self._seen.add(key)
        rid = len(self.requests) + 1
        self.requests.append(types.SimpleNamespace(id=rid, req=req))
        return True

    def dispose_authority_change_request(self, request_id, *, disposition, disposed_by, note=None):
        if request_id in self.dispositions:
            return False
        self.dispositions[request_id] = (disposition, disposed_by)
        return True

    def status_of(self, request_id):
        d = self.dispositions.get(request_id)
        return d[0] if d else STATUS_PENDING

    # park/notify are stubbed so we can exercise the real loop park site.
    def park_for_human(self, work_id, note=None):
        self.parked.append((work_id, note))

    def orphaned_runs(self):
        return []


def _fake_loop(inst, db):
    fake = types.SimpleNamespace(inst=inst, db=db)
    fake._instance_id = lambda: Loop._instance_id(fake)
    fake.notify_human = lambda work, reason=None: db.notified.append((work.id, reason))
    return fake


def _emit(fake, work):
    return Loop._emit_authority_change_requests(fake, work)


def _gate_reasons(inst, work):
    return Loop.human_gate_reasons(types.SimpleNamespace(inst=inst), work)


# -- action_digest ------------------------------------------------------------------------------
def test_action_digest_is_stable_across_calls():
    plan = {"steps": [{"step_id": "s1", "action": "send email to the customer"}]}
    assert action_digest(plan) == action_digest(dict(plan))  # same content -> same digest


def test_action_digest_differs_by_plan():
    a = action_digest({"steps": [{"action": "send email to the customer"}]})
    b = action_digest({"steps": [{"action": "charge the customer"}]})
    assert a != b


# -- build_requests_from_verdict ----------------------------------------------------------------
def test_non_gated_verdict_builds_nothing():
    verdict = assess_plan_envelope(_work("upsert the aqdb subscription record").plan,
                                   DEFAULT_ENVELOPE)
    assert verdict.gated is False
    assert build_requests_from_verdict(verdict, work=_work("x"), instance_id="i") == []


def test_gated_verdict_builds_one_bounded_pending_request_per_class():
    work = _work("send email to the customer")   # -> contact_human, default gated
    verdict = assess_plan_envelope(work.plan, DEFAULT_ENVELOPE)
    reqs = build_requests_from_verdict(verdict, work=work, instance_id="inst-A")
    assert [r.capability_class for r in reqs] == [CLASS_CONTACT_HUMAN]
    r = reqs[0]
    assert r.status == STATUS_PENDING
    assert r.reason_kind == REASON_GATED_CLASS
    assert r.instance_id == "inst-A"
    assert r.proposed_action_digest == action_digest(work.plan)
    assert "NOT a standing grant" in r.requested_bound
    # receipt shape (BB #2716): a proposal is NEVER self-authorized.
    receipt = r.to_receipt()
    assert receipt["verdict"] == "requested"
    assert receipt["internally_authorized"] is False
    assert receipt["status"] == STATUS_PENDING


# -- emit hook: flag OFF is inert --------------------------------------------------------------
def test_flag_off_emits_nothing(monkeypatch):
    monkeypatch.delenv("AQ_AUTHORITY_REQUESTS", raising=False)
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")   # gate ON, but request flag OFF
    db = FakeDb()
    fake = _fake_loop(_inst(), db)
    _emit(fake, _work("send email to the customer"))
    assert db.requests == []
    assert enabled({}) is False


# -- emit hook: flag ON emits exactly one, and dedupes across cycles ----------------------------
def test_flag_on_emits_one_request_and_dedupes(monkeypatch):
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    monkeypatch.setenv("AQ_AUTHORITY_REQUESTS", "1")
    db = FakeDb()
    fake = _fake_loop(_inst(), db)
    work = _work("send email to the customer")
    _emit(fake, work)
    _emit(fake, work)   # a second cycle re-parks the SAME gated action
    _emit(fake, work)
    assert len(db.requests) == 1, "dedupe: one open request per (instance, class, action-digest)"
    assert db.requests[0].req.capability_class == CLASS_CONTACT_HUMAN
    assert db.status_of(db.requests[0].id) == STATUS_PENDING


def test_flag_on_non_gated_work_emits_nothing(monkeypatch):
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    monkeypatch.setenv("AQ_AUTHORITY_REQUESTS", "1")
    db = FakeDb()
    fake = _fake_loop(_inst(), db)
    _emit(fake, _work("upsert the aqdb subscription record"))   # internal_reversible, not gated
    assert db.requests == []


# -- the request does NOT change the gate decision ----------------------------------------------
def test_request_flag_does_not_change_the_gate(monkeypatch):
    """With the gate ON, the categorical park reason is IDENTICAL whether or not the request flag is
    set — emitting the proposal is purely additive; the work still parks either way."""
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    work = _work("send email to the customer")
    monkeypatch.delenv("AQ_AUTHORITY_REQUESTS", raising=False)
    without = _gate_reasons(_inst(), work)
    monkeypatch.setenv("AQ_AUTHORITY_REQUESTS", "1")
    with_flag = _gate_reasons(_inst(), work)
    assert without == with_flag
    assert any("capability envelope gates" in r for r in with_flag)


def test_emitting_still_parks_the_work(monkeypatch):
    """The full park site: gate fires -> work parks -> request emitted ALONGSIDE (not instead)."""
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    monkeypatch.setenv("AQ_AUTHORITY_REQUESTS", "1")
    db = FakeDb()
    fake = _fake_loop(_inst(), db)
    work = _work("charge the customer", spends_money=True)   # spend_money, default gated
    # emulate the loop park site (park + notify + emit)
    reasons = _gate_reasons(fake.inst, work)
    assert reasons
    db.park_for_human(work.id, note="; ".join(reasons))
    fake.notify_human(work, reason="; ".join(reasons))
    _emit(fake, work)
    assert db.parked == [(work.id, db.parked[0][1])]          # work parked
    assert len(db.requests) == 1                              # AND a request emitted
    assert db.requests[0].req.capability_class == CLASS_SPEND_MONEY


# -- emit is FAIL-SAFE: a DB failure inside it must never crash the cycle -----------------------
def test_emit_is_fail_safe_when_db_raises(monkeypatch):
    """Emit is ADDITIVE and runs AFTER the durable park+notify. A DB failure inside it (connection
    dropped mid-emit, an unexpected error) must NEVER propagate to crash the cycle — matching every
    other additive/telemetry hook in loop.py (comms emit, demo replication, self-correction apply,
    beat_cycle, shadow), which are all locally fail-safe with 'must never crash/halt the loop'. The
    park already committed before emit runs, so a raise here cannot un-park; it must be swallowed."""
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    monkeypatch.setenv("AQ_AUTHORITY_REQUESTS", "1")

    class BoomDb(FakeDb):
        def emit_authority_change_request(self, req):
            raise RuntimeError("db connection dropped mid-emit")

    db = BoomDb()
    fake = _fake_loop(_inst(), db)
    # The park+notify already happened at the call site; emit must not re-raise and crash the cycle.
    _emit(fake, _work("charge the customer", spends_money=True))   # must NOT raise


# -- the actor cannot self-approve --------------------------------------------------------------
def test_request_is_born_unauthorized_and_has_no_actor_approve_path():
    """A freshly built request carries no authority: pending status, not internally authorized, and
    there is NO method on the request/loop that flips it to approved. Approval is the operator's
    dispose_* on a separate table (grant-backed boundary asserted in the PG test)."""
    work = _work("charge the customer", spends_money=True)
    verdict = assess_plan_envelope(work.plan, DEFAULT_ENVELOPE)
    req = build_requests_from_verdict(verdict, work=work, instance_id="i")[0]
    assert req.status == STATUS_PENDING
    assert req.to_receipt()["internally_authorized"] is False
    assert not hasattr(req, "approve")
    assert not hasattr(Loop, "approve_authority_change_request")   # no actor-side self-approve
