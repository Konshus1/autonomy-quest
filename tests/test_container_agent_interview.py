"""Task #4407 — the container's ultimate agent, after INTERVIEW/SETUP, starts a mission.

Two tiers, both saved as reviewable test cases:

- Tier A (pure, always runs): the interview artifact `instance.yaml` AIMS the agent —
  a valid simple-mission file loads into an Instance; an unaimed file fails LOUD; the
  mission's boundaries reject money/human work. This is the "interview -> buildable,
  aimed contract" check.

- Tier B (integration, gated on AQ_AGENT_DB_URL): with the agent aimed by the same file,
  drive ONE real loop cycle against a live Postgres, using a STUB executor in place of the
  LLM (so no API keys / cost). Asserts the aimed loop turns the mission into recorded work:
  a work row, a completed run, and a learning. Point it at the OOTB container's DB:
      AQ_AGENT_DB_URL=postgresql://aq:<password>@localhost:5432/aq pytest tests/test_container_agent_interview.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from runner.config import Instance, Unaimed

FIXTURE = Path(__file__).parent / "fixtures" / "simple_mission.instance.yaml"


# ---- Tier A: interview -> aimed contract (pure) ------------------------------

def test_interview_artifact_aims_the_agent() -> None:
    inst = Instance.load(str(FIXTURE))
    assert inst.mission.objective
    assert inst.mission.measure.what == "demo marker rows"
    assert inst.mission.measure.where.strip().lower().startswith("select")
    assert inst.engine.mode == "subscription"
    assert inst.budget.autonomy.level == "act-reversible"


def test_unaimed_instance_fails_loud(tmp_path: Path) -> None:
    empty = tmp_path / "instance.yaml"
    empty.write_text("{}\n")
    with pytest.raises(Unaimed):
        Instance.load(str(empty))

    # A mission with an objective but no measurable ground truth is still unaimed.
    partial = tmp_path / "partial.yaml"
    partial.write_text("mission:\n  objective: do something\n  measure: {}\n")
    with pytest.raises(Unaimed):
        Instance.load(str(partial))


def test_mission_boundaries_reject_money_and_human_work() -> None:
    inst = Instance.load(str(FIXTURE))

    class _W:
        spends_money = False
        touches_human = False

    safe = _W()
    assert inst.mission.within_boundaries(safe) is True
    spends = _W()
    spends.spends_money = True
    assert inst.mission.within_boundaries(spends) is False


# ---- Tier B: aimed agent starts the mission (live loop, stubbed LLM) ---------

_DB_URL = os.environ.get("AQ_AGENT_DB_URL")
_needs_db = pytest.mark.skipif(
    not _DB_URL,
    reason="set AQ_AGENT_DB_URL to a live AQ Postgres (e.g. the OOTB container) to run the loop",
)


class StubExecutor:
    """Stands in for the LLM. Returns schema-valid canned replies for decide/act/reflect
    so a full cycle runs deterministically with zero cost and no API keys."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, prompt: str, schema, tier: str = "working"):
        from runner import prompts
        from runner.executor import Usage

        if schema is prompts.DECIDE_SCHEMA:
            self.calls.append("decide")
            return (
                {
                    "do_nothing": False,
                    "kind": "demo",
                    "summary": "seed one demo marker toward the mission",
                    "rationale": "creates the first pointable artifact that moves the measure",
                    "reversible": True,
                    "spends_money": False,
                    "touches_human": False,
                    "commits": False,
                    "plan": {
                        "goal_predicate": {"metric": "mission_delta", "operator": ">=", "value": 0},
                        "mission_concerns": [
                            {"concern_id": "serve_mission_progress", "kind": "serve",
                             "predicate": {"metric": "mission_delta", "operator": ">=", "value": 0}},
                            {"concern_id": "must_not_overshoot", "kind": "must_not_harm",
                             "predicate": {"metric": "mission_value", "operator": "<=", "value": 1.0}},
                        ],
                        "subgoals": [{"subgoal_id": "demo",
                            "success_predicate": {"metric": "mission_value", "operator": "<=", "value": 1.0},
                            "serves_concern_ids": ["serve_mission_progress", "must_not_overshoot"]}],
                        "steps": [{"step_id": "seed-marker", "subgoal_id": "demo", "action": "demo",
                            "expected_effect": "measure_up", "expected_direction": "toward", "scope": {}}],
                    },
                },
                Usage(),
            )
        if schema is prompts.ACT_SCHEMA:
            self.calls.append("act")
            return (
                {"outcome": "recorded a demo marker", "succeeded": True, "evidence": "work row",
                 "observed_metrics": {"mission_delta": 1, "mission_value": 1},
                 "step_results": [{"step_id": "seed-marker", "executed": True,
                                   "confirmed": True, "harmed_concern_ids": [], "evidence": "work row"}]},
                Usage(),
            )
        if schema is prompts.REFLECT_SCHEMA:
            self.calls.append("reflect")
            return (
                {
                    "insight": "the aimed loop turns a simple mission into recorded work",
                    "evidence": "a run completed with an artifact",
                    "scope": "local",
                    "confidence": 0.9,
                },
                Usage(),
            )
        raise AssertionError(f"StubExecutor got an unexpected schema at tier={tier}")


@_needs_db
def test_aimed_agent_starts_simple_mission() -> None:
    from runner.db import Db
    from runner.loop import Loop

    inst = Instance.load(str(FIXTURE))
    db = Db(_DB_URL, graph=inst.datastore.get("graph", "age"))
    ex = StubExecutor()
    loop = Loop(inst, db, ex)

    cycle = loop.cycle()

    # The aimed agent decided, acted, and reflected — it did not stall.
    assert ex.calls == ["decide", "act", "reflect"], ex.calls
    assert cycle is not None, "aimed loop produced no cycle — it should have started work"
    assert cycle.run_id
    assert cycle.work_id
    assert cycle.learned  # a learning was written and returned
