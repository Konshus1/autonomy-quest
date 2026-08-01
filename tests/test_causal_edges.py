"""Task #4407 slice 1a — causal-edge contract + planning-certainty + surprise (BB #746)."""

from __future__ import annotations

import pytest

from ralph_portable.causal_edges import (
    edge_certainty,
    edge_identity,
    is_guaranteed,
    plan_certainty,
    surprise,
    validate_causal_edge,
)


def _edge(**over):
    e = {
        "cause": "send_followup",
        "effect": "email_sent",
        "formality": "evidential",
        "strictness": "advisory",
        "directness": "predicate",
        "executor": {"kind": "predicate", "ref": "checks/email_sent.sql"},
        "predicted_certainty": 0.7,
    }
    e.update(over)
    return e


# ---- validation ----

def test_valid_edge_passes():
    assert validate_causal_edge(_edge()) == []


def test_required_and_enum_fields():
    assert "cause is required (the action and its direct effect)" in "; ".join(
        validate_causal_edge(_edge(cause=""))
    )
    assert any("formality must be" in e for e in validate_causal_edge(_edge(formality="maybe")))
    assert any("strictness must be" in e for e in validate_causal_edge(_edge(strictness="kinda")))
    assert any("directness must be" in e for e in validate_causal_edge(_edge(directness="sorta")))


def test_executor_ref_required_for_script_and_predicate():
    assert any("requires a 'ref'" in e for e in validate_causal_edge(
        _edge(directness="script", executor={"kind": "script"})))
    # judgment executor needs no ref
    assert validate_causal_edge(_edge(
        formality="fuzzy", directness="judgment", executor={"kind": "judgment"})) == []


def test_predicted_certainty_range():
    assert any("predicted_certainty" in e for e in validate_causal_edge(_edge(predicted_certainty=1.5)))
    assert any("predicted_certainty" in e for e in validate_causal_edge(_edge(predicted_certainty="high")))


def test_formal_cannot_be_judgment_honesty_guard():
    errs = validate_causal_edge(_edge(formality="formal", directness="judgment",
                                      executor={"kind": "judgment"}))
    assert any("formal edge cannot have directness=judgment" in e for e in errs)


# ---- identity (immutable; ccf-1557 C2 evidence-vs-identity invariant) ----

def test_edge_identity_is_immutable_under_state_change():
    base = _edge(cause="a", effect="e1", scope={"region": "us"})
    ident = edge_identity(base)
    # mutating self-advancing STATE must not change identity
    for mutated in (
        _edge(cause="a", effect="e1", scope={"region": "us"}, formality="formal",
              directness="script", executor={"kind": "script", "ref": "s.py"}),
        _edge(cause="a", effect="e1", scope={"region": "us"}, predicted_certainty=0.99,
              support_count=500, last_validated="2026-08-01", falsified_by="x"),
    ):
        assert edge_identity(mutated) == ident
    # scope key is order-independent
    assert edge_identity(_edge(cause="a", effect="e1", scope={"a": 1, "b": 2})) == \
        edge_identity(_edge(cause="a", effect="e1", scope={"b": 2, "a": 1}))
    # different cause/effect/scope => different identity
    assert edge_identity(_edge(cause="a", effect="e2")) != ident
    assert edge_identity(_edge(cause="a", effect="e1", scope={"region": "eu"})) != ident


# ---- certainty ----

def test_edge_certainty_capped_by_formality():
    # a fuzzy edge with an inflated predicted_certainty is capped at the fuzzy weight
    fuzzy = _edge(formality="fuzzy", directness="judgment",
                  executor={"kind": "judgment"}, predicted_certainty=0.99)
    assert edge_certainty(fuzzy) <= 0.34 + 1e-9
    # a formal script edge without predicted_certainty falls back to 1.0
    formal = _edge(formality="formal", directness="script",
                   executor={"kind": "script", "ref": "scripts/send.py"})
    formal.pop("predicted_certainty")
    assert edge_certainty(formal) == 1.0


def test_is_guaranteed_only_formal_script():
    assert is_guaranteed(_edge(formality="formal", directness="script",
                               executor={"kind": "script", "ref": "s.py"})) is True
    assert is_guaranteed(_edge(formality="formal", directness="predicate")) is False
    assert is_guaranteed(_edge(formality="evidential", directness="script",
                               executor={"kind": "script", "ref": "s.py"})) is False


# ---- planning certainty ----

def test_plan_certainty_profile():
    edges = [
        _edge(cause="a", effect="e1", formality="formal", directness="script",
              executor={"kind": "script", "ref": "s.py"}, predicted_certainty=0.95),
        _edge(cause="b", effect="e2", formality="fuzzy", directness="judgment",
              executor={"kind": "judgment"}),  # fuzzy -> capped low
    ]
    steps = [
        {"action": "a", "effect": "e1"},   # covered + guaranteed
        {"action": "b", "effect": "e2"},   # covered, fuzzy
        {"action": "c", "effect": "e3"},   # uncovered
    ]
    prof = plan_certainty(steps, edges)
    assert prof["steps"] == 3
    assert prof["covered"] == 2
    assert prof["uncovered"] == 1
    assert prof["guaranteed_fraction"] == round(1 / 3, 4)
    assert prof["per_step"][0]["guaranteed"] is True
    assert prof["per_step"][1]["guaranteed"] is False
    assert prof["per_step"][2]["covered"] is False


def test_plan_certainty_ignores_malformed_edges():
    edges = [{"cause": "a", "effect": "e1"}]  # missing dials -> malformed
    prof = plan_certainty([{"action": "a", "effect": "e1"}], edges)
    assert prof["covered"] == 0


# ---- surprise ----

def test_surprise_confirm_investigate_demote():
    assert surprise(0.8, 0.9)["signal"] == "confirm"       # outcome succeeded
    assert surprise(0.6, True)["signal"] == "confirm"      # succeeded even if predicted modestly
    assert surprise(0.4, 0.0)["signal"] == "investigate"   # uncertain prediction, failed
    assert surprise(0.9, 0.0)["signal"] == "demote"        # confident + wrong
    s = surprise(0.9, True)
    assert s["actual"] == 1.0 and s["surprise"] == round(0.1, 4)
    assert s["gated"] is True and s["surprise_type"] == "planning_prediction_surprise"


def test_surprise_range_guard():
    with pytest.raises(ValueError):
        surprise(1.4, 0.5)
