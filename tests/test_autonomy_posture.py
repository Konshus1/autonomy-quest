from decimal import Decimal
from types import SimpleNamespace

from runner.db import Work
from runner.loop import Loop
from runner import prompts


def _loop(level="act-external", cost_gate=3.0, blast_gate=0.8):
    loop = Loop.__new__(Loop)
    loop.inst = SimpleNamespace(
        budget=SimpleNamespace(
            autonomy=SimpleNamespace(
                level=level,
                expected_plan_cost_gate_usd=cost_gate,
                blast_radius_gate=blast_gate,
            )
        )
    )
    return loop


def _work(**overrides):
    values = dict(id=1, kind="outreach", summary="contact one prospect", rationale="test",
                  reversible=True, spends_money=False, expected_cost_usd=Decimal("0"),
                  blast_radius=0.1, touches_human=True, commits=False)
    values.update(overrides)
    return Work(**values)


def test_five_dollar_plan_gates_and_one_dollar_plan_does_not():
    loop = _loop()
    five = _work(spends_money=True, expected_cost_usd=Decimal("5"))
    one = _work(spends_money=True, expected_cost_usd=Decimal("1"))
    assert loop.requires_human(five) is True
    assert loop.requires_human(one) is False
    assert loop.human_gate_reasons(five) == ["expected plan cost $5 is over $3.0"]
    assert loop.human_gate_reasons(one) == []


def test_touching_human_or_committing_is_not_a_category_gate():
    loop = _loop()
    assert loop.requires_human(_work(touches_human=True, commits=True)) is False


def test_high_blast_radius_gates_at_configured_boundary():
    loop = _loop()
    assert loop.requires_human(_work(blast_radius=0.79)) is False
    assert loop.requires_human(_work(blast_radius=0.80)) is True


def test_decision_schema_requires_cost_and_blast_radius():
    assert "expected_cost_usd" in prompts.DECIDE_SCHEMA["required"]
    assert "blast_radius" in prompts.DECIDE_SCHEMA["required"]
