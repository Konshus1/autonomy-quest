"""Authority envelope (design_authority_envelope.md Slice 2; BB #2716).

Operator decision (Kevin 2026-08-15): GATED BY DEFAULT, operator can OPEN gates, gates easily
configurable, actor can only REQUEST. This suite proves:

  * config: absent section => defaults; opening a class is one declarative line; an unknown class
    name or disposition fails LOUD (fail-closed), never silently ignored.
  * classification: real work-flag bindings + operator boundary + best-effort keyword map; opaque
    fails closed.
  * RED-FIRST gate wiring: flag ON + DEFAULT envelope => a spends_money/touches_human/commit action
    gates categorically even at model-declared blast 0 (and does NOT on the flag-OFF path -- the gap);
    an operator config OPENING that class lets it through to the numeric gate; an internal reversible
    aqdb write stays auto.
  * flag OFF => human_gate_reasons is byte-identical (no categorical reason).
"""
import textwrap
import types
from decimal import Decimal

import pytest

from runner.authority_envelope import (
    ALL_CLASSES, CLASS_CONTACT_HUMAN, CLASS_INTERNAL_REVERSIBLE, CLASS_SPEND_MONEY,
    DEFAULT_ENVELOPE, DISPOSITION_GATED, DISPOSITION_OPEN, EnvelopeConfigError,
    HIGH_CONSEQUENCE_CLASSES, assess_action_envelope, assess_plan_envelope,
    classify_capability_classes, enabled, from_config,
)
from runner.config import Instance
from runner.loop import Loop


# ==============================================================================================
# config loader + validation
# ==============================================================================================
def test_absent_section_is_full_defaults():
    env = from_config(None)
    for c in HIGH_CONSEQUENCE_CLASSES:
        assert env.is_gated(c) is True
    assert env.is_gated(CLASS_INTERNAL_REVERSIBLE) is False


def test_default_envelope_matches_absent_section():
    assert DEFAULT_ENVELOPE.as_dict() == from_config(None).as_dict()


def test_operator_opens_one_class_others_keep_defaults():
    env = from_config({CLASS_SPEND_MONEY: "open"})
    assert env.is_gated(CLASS_SPEND_MONEY) is False           # opened
    assert env.is_gated(CLASS_CONTACT_HUMAN) is True           # untouched => default gated
    assert env.is_gated(CLASS_INTERNAL_REVERSIBLE) is False    # default open


def test_operator_can_close_internal_reversible():
    env = from_config({CLASS_INTERNAL_REVERSIBLE: "gated"})
    assert env.is_gated(CLASS_INTERNAL_REVERSIBLE) is True


def test_unknown_class_name_fails_loud():
    with pytest.raises(EnvelopeConfigError):
        from_config({"send_emails": "open"})


def test_unknown_disposition_fails_loud():
    with pytest.raises(EnvelopeConfigError):
        from_config({CLASS_SPEND_MONEY: "sometimes"})


def test_non_mapping_section_fails_loud():
    with pytest.raises(EnvelopeConfigError):
        from_config(["spend_money"])


def test_disposition_is_case_insensitive():
    assert from_config({CLASS_SPEND_MONEY: "OPEN"}).is_gated(CLASS_SPEND_MONEY) is False


def test_disposition_fail_closed_on_unspecified_class():
    # An envelope constructed with a partial dict still treats an unknown key as gated.
    from runner.authority_envelope import AuthorityEnvelope
    env = AuthorityEnvelope({CLASS_SPEND_MONEY: DISPOSITION_OPEN})
    assert env.is_gated(CLASS_CONTACT_HUMAN) is True           # absent => gated (fail-closed)
    assert env.is_gated(CLASS_SPEND_MONEY) is False


# -- config surfaces through Instance.load (validated even with flag OFF) -----------------------
_MISSION_YAML = """
mission:
  objective: "test"
  measure:
    what: "count"
    where: "select 1"
    target: 10
budget:
  autonomy:
    level: act-external
surfaces:
  notify:
    channel: none
"""


def _write_instance(tmp_path, extra=""):
    p = tmp_path / "instance.yaml"
    p.write_text(textwrap.dedent(_MISSION_YAML) + textwrap.dedent(extra))
    return str(p)


def test_instance_load_default_envelope(tmp_path):
    inst = Instance.load(_write_instance(tmp_path))
    assert inst.authority_envelope.is_gated(CLASS_SPEND_MONEY) is True
    assert inst.authority_envelope.is_gated(CLASS_INTERNAL_REVERSIBLE) is False


def test_instance_load_operator_opened_class(tmp_path):
    inst = Instance.load(_write_instance(tmp_path, "\nauthority_envelope:\n  spend_money: open\n"))
    assert inst.authority_envelope.is_gated(CLASS_SPEND_MONEY) is False
    assert inst.authority_envelope.is_gated(CLASS_CONTACT_HUMAN) is True


def test_instance_load_bad_class_fails_loud_even_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("AQ_AUTHORITY_ENVELOPE", raising=False)
    with pytest.raises(EnvelopeConfigError):
        Instance.load(_write_instance(tmp_path, "\nauthority_envelope:\n  nonsense_class: open\n"))


# ==============================================================================================
# classification
# ==============================================================================================
@pytest.mark.parametrize("flag,cls", [
    ("spends_money", CLASS_SPEND_MONEY), ("touches_human", CLASS_CONTACT_HUMAN),
    ("commits", "commit"),
])
def test_work_flags_map_to_real_classes(flag, cls):
    c = classify_capability_classes("do a thing", **{flag: True})
    assert cls in c.classes


def test_opaque_action_fails_closed():
    c = classify_capability_classes(None)
    assert c.fail_closed is True


@pytest.mark.parametrize("action,cls", [
    ("send email to the customer", CLASS_CONTACT_HUMAN),
    ("publish to twitter the launch", "publish_external"),
    ("deploy to production now", "deploy"),
    ("fire an http post to the external api", "external_write"),
    ("take payment from the customer", CLASS_SPEND_MONEY),
    ("drop the table in prod", "delete_prod"),
    ("git commit and push to origin", "commit"),
])
def test_keyword_map_classes(action, cls):
    assert cls in classify_capability_classes(action).classes


def test_internal_keyword_only_when_nothing_high():
    c = classify_capability_classes("upsert the customer subscription record in aqdb")
    assert c.classes == (CLASS_INTERNAL_REVERSIBLE,)


def test_internal_never_cancels_high_consequence():
    # both an internal keyword ("aqdb") and a spend flag: internal must NOT appear.
    c = classify_capability_classes("record in aqdb then charge the customer", spends_money=True)
    assert CLASS_SPEND_MONEY in c.classes
    assert CLASS_INTERNAL_REVERSIBLE not in c.classes


def test_must_ask_first_hit_flagged():
    c = classify_capability_classes("spend money moving funds", must_ask_first=["spend money"])
    assert c.must_ask_first_hit is True


# ==============================================================================================
# categorical envelope verdict
# ==============================================================================================
def test_default_envelope_gates_spend_money_categorically():
    v = assess_action_envelope("do a thing", DEFAULT_ENVELOPE, spends_money=True)
    assert v.gated is True and CLASS_SPEND_MONEY in v.gated_classes


def test_opened_class_falls_through_to_numeric():
    env = from_config({CLASS_SPEND_MONEY: "open"})
    v = assess_action_envelope("do a thing", env, spends_money=True)
    assert v.gated is False and CLASS_SPEND_MONEY in v.open_classes


def test_internal_reversible_action_not_gated():
    v = assess_action_envelope("upsert the customer record in aqdb", DEFAULT_ENVELOPE)
    assert v.gated is False


def test_must_ask_first_cannot_be_opened_by_envelope():
    # even opening every high-consequence class, a must_ask_first hit still gates.
    env = from_config({c: "open" for c in HIGH_CONSEQUENCE_CLASSES})
    v = assess_action_envelope("export the report", env, must_ask_first=["export"])
    assert v.gated is True and v.must_ask_first_hit is True


def test_opaque_action_gated_regardless_of_envelope():
    env = from_config({c: "open" for c in HIGH_CONSEQUENCE_CLASSES})
    v = assess_action_envelope(None, env)
    assert v.gated is True and v.fail_closed is True


def test_plan_gates_if_any_step_gates():
    plan = {"steps": [{"action": "query aqdb"}, {"action": "send email to the customer"}]}
    assert assess_plan_envelope(plan, DEFAULT_ENVELOPE).gated is True


def test_empty_plan_fails_closed():
    assert assess_plan_envelope({"steps": []}, DEFAULT_ENVELOPE).gated is True


def test_verdict_to_authority_policy_shape():
    v = assess_action_envelope("do a thing", DEFAULT_ENVELOPE, spends_money=True)
    ap = v.to_authority_policy()
    assert ap["verdict"] == "gated" and ap["categorically_gated"] is True
    assert "spend_money" in ap["gated_classes"] and ap["policy_version"]


# ==============================================================================================
# GATE wiring -- human_gate_reasons -- RED-FIRST
# ==============================================================================================
def facts(n=1):
    return {"affected_entities_upper_bound": n, "public_or_unbounded": False,
            "production_wide": False, "irreversible_external_write": False}


def _inst(envelope=DEFAULT_ENVELOPE, must_ask=(), gate_level=3, level="act-external", approval=3.0):
    autonomy = types.SimpleNamespace(level=level, blast_radius_gate_level=gate_level,
                                     per_plan_approval_usd=approval)
    return types.SimpleNamespace(
        budget=types.SimpleNamespace(autonomy=autonomy),
        mission=types.SimpleNamespace(boundaries=types.SimpleNamespace(must_ask_first=list(must_ask))),
        authority_envelope=envelope)


def _work(action, blast_level=0, spends_money=False, touches_human=False, commits=False,
          expense="0", reversible=True, requires_human=False):
    return types.SimpleNamespace(
        requires_human=requires_human, reversible=reversible, expected_expense_usd=Decimal(expense),
        blast_radius_level=blast_level, spends_money=spends_money, touches_human=touches_human,
        commits=commits,
        plan={"steps": [{"step_id": "s1", "action": action, "blast_radius": facts(1)}]})


def _reasons(work, inst):
    return Loop.human_gate_reasons(types.SimpleNamespace(inst=inst), work)


def _has_categorical(reasons):
    return any("capability envelope gates" in r for r in reasons)


@pytest.mark.parametrize("flag", ["spends_money", "touches_human", "commits"])
def test_flag_off_underdeclared_high_consequence_not_gated(monkeypatch, flag):
    """Byte-identical OFF path: the coarse flag alone does NOT gate a blast-0 action (the gap)."""
    monkeypatch.delenv("AQ_AUTHORITY_ENVELOPE", raising=False)
    reasons = _reasons(_work("do the thing", blast_level=0, **{flag: True}), _inst())
    assert reasons == []


@pytest.mark.parametrize("flag", ["spends_money", "touches_human", "commits"])
def test_flag_on_default_envelope_gates_high_consequence_at_blast_zero(monkeypatch, flag):
    """RED->GREEN: flag ON + DEFAULT envelope => categorical gate even at model-declared blast 0."""
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    reasons = _reasons(_work("do the thing", blast_level=0, **{flag: True}), _inst())
    assert _has_categorical(reasons)


def test_flag_on_operator_opened_class_falls_through_to_numeric(monkeypatch):
    """Operator OPENs spend_money => a blast-0 spends_money action is NOT categorically gated and,
    being blast 0 / $0, falls all the way through to auto."""
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    inst = _inst(envelope=from_config({CLASS_SPEND_MONEY: "open"}))
    reasons = _reasons(_work("do the thing", blast_level=0, spends_money=True), inst)
    assert reasons == []


def test_flag_on_opened_class_still_hits_numeric_gate_when_blast_high(monkeypatch):
    """Opening a class does NOT disable the numeric gate: a high-blast step still gates numerically."""
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    inst = _inst(envelope=from_config({CLASS_SPEND_MONEY: "open"}))
    reasons = _reasons(_work("do the thing", blast_level=3, spends_money=True), inst)
    assert reasons and not _has_categorical(reasons)  # numeric reason, not categorical


def test_flag_on_internal_reversible_aqdb_write_stays_auto(monkeypatch):
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    reasons = _reasons(_work("upsert the customer subscription record in aqdb", blast_level=0),
                       _inst())
    assert reasons == []


def test_flag_on_keyword_derived_external_gates_categorically(monkeypatch):
    """Keyword-only (no work flag) external action gates when its class is default-gated."""
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    reasons = _reasons(_work("send email to the customer", blast_level=0), _inst())
    assert _has_categorical(reasons)


def test_flag_on_must_ask_first_gates_even_with_all_classes_open(monkeypatch):
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    inst = _inst(envelope=from_config({c: "open" for c in HIGH_CONSEQUENCE_CLASSES}),
                 must_ask=["export"])
    reasons = _reasons(_work("export the weekly report", blast_level=0), inst)
    assert _has_categorical(reasons)


def test_flag_off_external_keyword_not_categorically_gated(monkeypatch):
    """Flag OFF => even an obvious external keyword adds no categorical reason (byte-identical)."""
    monkeypatch.delenv("AQ_AUTHORITY_ENVELOPE", raising=False)
    reasons = _reasons(_work("send email to the customer", blast_level=0), _inst())
    assert not _has_categorical(reasons)


# ==============================================================================================
# flag helper
# ==============================================================================================
@pytest.mark.parametrize("val,want", [("1", True), ("true", True), ("on", True),
                                      ("0", False), ("", False), ("no", False)])
def test_enabled_reads_flag(val, want):
    assert enabled({"AQ_AUTHORITY_ENVELOPE": val}) is want


def test_enabled_default_off():
    assert enabled({}) is False
