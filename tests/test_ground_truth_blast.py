"""Ground-truth blast ratchet (design_authority_envelope.md Slice 1; BB #2716).

The consequence gate and the execute-to-learn predicate both derive an action's consequence level
from the MODEL's self-declared per-step ``blast_radius`` dict -- a declaration-trust gap. This suite
proves the flag-gated (``AQ_GROUND_TRUTH_BLAST``) one-way ratchet closes it:

  * RED-FIRST: an action with a high-consequence CAPABILITY signal but a model-declared LOW blast is
    rated level 3 / gated when the flag is ON (and, proving the pre-fix gap, is NOT gated on the
    default/OFF path).
  * a genuinely internal reversible aqdb write still rates low.
  * flag OFF is byte-identical to pre-Slice-1 behavior.
"""
import types

import pytest

from runner.capability_consequence import (
    IRREVERSIBLE_EXTERNAL, CapabilityContext, classify_capability, effective_blast, enabled,
)
from runner.consequence_gate import assess_plan_gate
from runner.loop import Loop


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------
def facts(n=0, public=False, production=False, irreversible=False):
    return {"affected_entities_upper_bound": n, "public_or_unbounded": public,
            "production_wide": production, "irreversible_external_write": irreversible}


def step(action="record an internal note", blast=None):
    return {"step_id": "s1", "action": action, "blast_radius": blast if blast is not None else facts(1)}


def plan(steps, cost=0):
    return {"expected_expense_usd": cost, "steps": steps}


# --------------------------------------------------------------------------------------------
# module: classify_capability -- CODE-OWNED, reads no model risk label
# --------------------------------------------------------------------------------------------
def test_classify_opaque_action_fails_closed_level3():
    lvl, cls, _ = classify_capability(None)
    assert lvl == IRREVERSIBLE_EXTERNAL and cls == "irreversible_external"


@pytest.mark.parametrize("flag", ["spends_money", "touches_human", "commits"])
def test_classify_work_flags_ratchet_to_external(flag):
    lvl, cls, reasons = classify_capability("do a thing", **{flag: True})
    assert lvl == IRREVERSIBLE_EXTERNAL and cls == "irreversible_external"
    assert any(flag.split("_")[0] in r.lower() or "commit" in r.lower() for r in reasons)


@pytest.mark.parametrize("action", [
    "Send email to the 40 prospects", "publish to twitter the launch note",
    "fire an http post to the external api", "take payment from the customer",
    "delete prod database rows", "phone call to the customer to confirm",
])
def test_classify_capability_keywords_ratchet_to_external(action):
    lvl, cls, _ = classify_capability(action)
    assert lvl == IRREVERSIBLE_EXTERNAL and cls == "irreversible_external"


def test_classify_must_ask_first_boundary_ratchets():
    lvl, cls, reasons = classify_capability("move money between the ledgers",
                                            must_ask_first=["money"])
    assert lvl == IRREVERSIBLE_EXTERNAL
    assert any("must_ask_first" in r for r in reasons)


def test_classify_internal_aqdb_write_is_low_internal():
    lvl, cls, _ = classify_capability("upsert the customer subscription record in aqdb")
    assert lvl == 0 and cls == "internal"


# --------------------------------------------------------------------------------------------
# module: effective_blast -- the one-way ratchet
# --------------------------------------------------------------------------------------------
def test_ratchet_raises_underdeclared_external_step():
    # model says single-entity (level 1); capability (send email) says external (3) -> effective 3.
    v = effective_blast(step(action="send email to the customer", blast=facts(1)))
    assert v.model_declared_level == 1 and v.capability_level == 3 and v.effective_level == 3
    assert v.reversible is False and v.low_consequence is False


def test_ratchet_never_lowers_model_declared_high():
    # model already says level 3 (irreversible); capability is a benign internal note -> stays 3.
    v = effective_blast(step(action="record an internal note", blast=facts(1, irreversible=True)))
    assert v.model_declared_level == 3 and v.capability_level == 0 and v.effective_level == 3


def test_ratchet_internal_reversible_stays_low_and_typed():
    v = effective_blast(step(action="query aqdb read-only for counts", blast=facts(1)))
    assert v.effective_level == 1
    assert v.reversible is True and v.internally_authorized is True and v.low_consequence is True


def test_ratchet_opaque_step_fails_closed():
    assert effective_blast(None).effective_level == 3
    assert effective_blast({"action": "x"}).effective_level == 3  # missing blast dict -> assessor lvl 3


# --------------------------------------------------------------------------------------------
# GATE wiring -- the RED-FIRST proof
# --------------------------------------------------------------------------------------------
def test_gate_flag_off_underdeclared_external_passes():
    """Pre-fix / default path: an external action self-declared LOW blast is NOT gated (the gap)."""
    p = plan([step(action="send email to the customer", blast=facts(1))])
    d = assess_plan_gate(p)                       # no capability_context => OFF path
    assert d.requires_human is False and d.blast_radius_level == 1 and d.capability is None


def test_gate_flag_on_underdeclared_external_is_gated_level3():
    """RED-FIRST: SAME action, flag ON (context supplied) => ratcheted to level 3 and gated."""
    p = plan([step(action="send email to the customer", blast=facts(1))])
    d = assess_plan_gate(p, capability_context=CapabilityContext())
    assert d.requires_human is True and d.blast_radius_level == 3
    assert d.capability is not None and d.capability.effective_level == 3
    assert d.capability.reversible is False


def test_gate_flag_on_work_flag_channel_ratchets_even_with_clean_action():
    """The coarse work flag (separate channel) catches an under-declared blast dict."""
    p = plan([step(action="do the thing", blast=facts(1))])
    d = assess_plan_gate(p, capability_context=CapabilityContext(spends_money=True))
    assert d.requires_human is True and d.blast_radius_level == 3


def test_gate_flag_on_internal_reversible_aqdb_write_stays_low():
    """A genuinely internal reversible aqdb write still rates low with the flag ON."""
    p = plan([step(action="upsert the customer subscription record in aqdb", blast=facts(1))])
    d = assess_plan_gate(p, capability_context=CapabilityContext())
    assert d.requires_human is False and d.blast_radius_level == 1
    assert d.capability.internally_authorized is True and d.capability.low_consequence is True


# --------------------------------------------------------------------------------------------
# PREDICATE wiring (execute-to-learn) -- red-first
# --------------------------------------------------------------------------------------------
def _self():
    return types.SimpleNamespace(_EXECUTE_TO_LEARN_MAX_ENTITIES=10)


def _work(action, blast=None):
    return types.SimpleNamespace(
        requires_human=False, reversible=True, spends_money=False, touches_human=False, commits=False,
        plan={"steps": [{"step_id": "rec", "action": action,
                         "blast_radius": blast if blast is not None else facts(3)}]})


def _assess(ids):
    return types.SimpleNamespace(frame_gap_step_ids=tuple(ids))


def _qualifies(work):
    return Loop._frame_gaps_are_reversibly_self_observable(_self(), work, _assess(["rec"]))


def test_predicate_flag_off_underdeclared_external_still_qualifies(monkeypatch):
    """Byte-identical OFF path: the gap exists -- an external action tagged low-blast qualifies."""
    monkeypatch.delenv("AQ_GROUND_TRUTH_BLAST", raising=False)
    assert _qualifies(_work("send email to the customer", facts(3))) is True


def test_predicate_flag_on_underdeclared_external_disqualifies(monkeypatch):
    """RED-FIRST: flag ON -> the capability ratchet rates the step level 3 -> does NOT execute-to-learn."""
    monkeypatch.setenv("AQ_GROUND_TRUTH_BLAST", "1")
    assert _qualifies(_work("send email to the customer", facts(3))) is False


def test_predicate_flag_on_internal_reversible_still_qualifies(monkeypatch):
    """Not over-broad: a genuinely internal reversible low-blast step still executes-to-learn ON."""
    monkeypatch.setenv("AQ_GROUND_TRUTH_BLAST", "1")
    assert _qualifies(_work("record an internal note in aqdb", facts(3))) is True


# --------------------------------------------------------------------------------------------
# flag helper
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("val,want", [("1", True), ("true", True), ("on", True),
                                      ("0", False), ("", False), ("no", False)])
def test_enabled_reads_flag(val, want):
    assert enabled({"AQ_GROUND_TRUTH_BLAST": val}) is want


def test_enabled_default_off():
    assert enabled({}) is False
