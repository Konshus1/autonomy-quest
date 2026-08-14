"""Execute-to-learn cold-start bootstrap (#4834).

A frame gap = no LEARNED causal relation backs a plan step. The acquisition machinery resolves a
frame gap by GATHERING INFORMATION and must not perform the target action -- but a reversible,
mission-authorized, low-consequence action's effect can ONLY be learned by doing it reversibly and
observing. On a fresh mission the causal graph is empty, so EVERY step is a frame gap and the loop
DEADLOCKS: it never acts (routes to acquisition, which cannot perform the action) so it never learns.

The fix (flag-gated ``AQ_EXECUTE_TO_LEARN``, default OFF => byte-identical old behavior): a frame-gap
step that is reversible + internal + low-consequence proceeds to ACT; the observed before/after IS the
acquisition, and the consequence gate still fires as an independent backstop. Anything irreversible /
external / spending / human-touching still takes the full acquisition path.

RED-FIRST: ``test_flag_on_reversible_frame_gap_executes_to_learn`` FAILS against pre-fix code (the
work is always routed to acquisition => ``ex.meta_calls >= 1``); the fix makes it skip the forecast.
"""
import types

import pytest

from runner.loop import Loop
from tests.test_loop_approval_execution import instance
from tests.test_meta_mode_loop import MetaDb, MetaExecutor
from tests.test_resume_pending_quarantine import _frame_gap_plan, _pending_row, ResumeDb


# --------------------------------------------------------------------------------------------
# The predicate (pure) — exactly which frame-gap steps may execute-to-learn.
# --------------------------------------------------------------------------------------------
def _self():
    return types.SimpleNamespace(_EXECUTE_TO_LEARN_MAX_ENTITIES=10)


def _blast(**over):
    b = {"irreversible_external_write": False, "public_or_unbounded": False,
         "production_wide": False, "affected_entities_upper_bound": 3}
    b.update(over)
    return b


def _work(**over):
    base = dict(requires_human=False, reversible=True, spends_money=False, touches_human=False,
                commits=False, plan={"steps": [{"step_id": "rec", "blast_radius": _blast()}]})
    base.update(over)
    return types.SimpleNamespace(**base)


def _assess(ids):
    return types.SimpleNamespace(frame_gap_step_ids=tuple(ids))


def _qualifies(work, ids):
    return Loop._frame_gaps_are_reversibly_self_observable(_self(), work, _assess(ids))


def test_predicate_qualifies_reversible_internal_low_blast():
    assert _qualifies(_work(), ["rec"]) is True


@pytest.mark.parametrize("field", ["irreversible_external_write", "public_or_unbounded", "production_wide"])
def test_predicate_rejects_external_or_irreversible_step(field):
    w = _work()
    w.plan["steps"][0]["blast_radius"][field] = True
    assert _qualifies(w, ["rec"]) is False


def test_predicate_rejects_high_entity_count():
    w = _work()
    w.plan["steps"][0]["blast_radius"]["affected_entities_upper_bound"] = 11
    assert _qualifies(w, ["rec"]) is False


@pytest.mark.parametrize("flag,val", [("reversible", False), ("spends_money", True),
                                      ("touches_human", True), ("commits", True),
                                      ("requires_human", True)])
def test_predicate_rejects_on_work_consequence_flags(flag, val):
    assert _qualifies(_work(**{flag: val}), ["rec"]) is False


def test_predicate_rejects_legacy_plan_without_blast_facts():
    # Legacy approved work synthesizes steps with no per-step blast_radius -> must not qualify.
    assert _qualifies(_work(plan={"steps": [{"step_id": "rec"}]}), ["rec"]) is False


def test_predicate_rejects_if_any_frame_gap_step_disqualifies():
    w = _work(plan={"steps": [
        {"step_id": "ok", "blast_radius": _blast(affected_entities_upper_bound=1)},
        {"step_id": "bad", "blast_radius": _blast(irreversible_external_write=True)},
    ]})
    assert _qualifies(w, ["ok", "bad"]) is False  # conservative: one bad step -> acquisition


# --------------------------------------------------------------------------------------------
# The routing (full cycle) — the behavior change, flag OFF vs ON.
# --------------------------------------------------------------------------------------------
def _loop_with_frame_gap(**row_over):
    db = ResumeDb(_pending_row(_frame_gap_plan(), **row_over))
    ex = MetaExecutor(db)
    return Loop(instance(), db, ex), db, ex


def test_flag_off_reversible_frame_gap_routes_to_acquisition(monkeypatch):
    """Default OFF: byte-identical old behavior — a reversible frame gap still runs the forecast."""
    monkeypatch.delenv("AQ_EXECUTE_TO_LEARN", raising=False)
    loop, db, ex = _loop_with_frame_gap()
    loop.cycle()
    assert ex.meta_calls >= 1                      # the acquisition forecast ran
    assert db.acquisitions                         # an acquisition was prepared


def test_flag_on_reversible_frame_gap_executes_to_learn(monkeypatch):
    """RED-FIRST: with the flag on, a reversible + internal + low-blast frame gap SKIPS acquisition
    and proceeds to ACT — the forecast is never called."""
    monkeypatch.setenv("AQ_EXECUTE_TO_LEARN", "1")
    loop, db, ex = _loop_with_frame_gap()
    loop.cycle()
    assert ex.meta_calls == 0                      # acquisition forecast SKIPPED
    assert not db.acquisitions                     # no acquisition prepared
    assert "act" in db.events                      # the work actually executed


def test_flag_on_irreversible_frame_gap_still_routes_to_acquisition(monkeypatch):
    """The predicate is not over-broad: a non-reversible frame gap still takes acquisition even ON."""
    monkeypatch.setenv("AQ_EXECUTE_TO_LEARN", "1")
    loop, db, ex = _loop_with_frame_gap(reversible=False)
    loop.cycle()
    assert ex.meta_calls >= 1
    assert db.acquisitions
