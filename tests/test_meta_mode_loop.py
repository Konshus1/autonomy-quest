import pytest
from runner import prompts
from runner.acquisition import acquisition_step_for_mode
from runner.executor import Usage
from runner.db import Db
from runner.loop import Loop
from tests.test_loop_approval_execution import FakeDb, instance
from tests.test_pre_act_predictions import PlannedExecutor


class MetaDb(FakeDb):
    def __init__(self):
        super().__init__()
        self.meta_decisions = []
        self.meta_attempts = []
        self.rejected_attempts = []

    def meta_mode_observations(self, work_id, target_step_id):
        return [{"acquisition_id": a["acquisition_id"], "rung": a["rung"],
                 "result": a.get("result")}
                for a in self.acquisitions if a["work_id"] == work_id
                and a["target_step_id"] == target_step_id and a["status"] == "completed"]

    def complete_acquisition(self, cur, acquisition_id, result):
        super().complete_acquisition(cur, acquisition_id, result)
        if self.autonomous_pending:
            for key in ("acquisition_id", "acquisition_rung", "acquisition_instruction",
                        "acquisition_expense", "acquisition_blast", "acquisition_reversible",
                        "acquisition_spends", "acquisition_human", "acquisition_commits"):
                self.autonomous_pending.pop(key, None)

    def record_meta_mode_forecast_attempt(self, work_id, plan_id, target_step_id, forecast, usage):
        self.meta_attempts.append({"forecast": forecast, "usage": usage, "decision_id": None})
        return len(self.meta_attempts)

    def reject_meta_mode_forecast_attempt(self, attempt_id, error):
        self.rejected_attempts.append((attempt_id, error))
        self.autonomous_pending = None

    def quarantine_acquisition_receipt(self, run_id, acquisition_id, error, decision_id):
        self.events.append("acquisition_quarantined")
        for acquisition in self.acquisitions:
            if acquisition["acquisition_id"] == acquisition_id:
                acquisition["status"] = "skipped"
                acquisition["result"] = {"receipt_violation": error}
        self.autonomous_pending = None

    def prepare_meta_mode_decision(self, work_id, plan_id, target_step_id, decision, usage,
                                   attempt_id=None):
        self.meta_decisions.append(decision.json())
        self.events.append("meta_decision_persisted")
        if decision.decision != "acquire":
            self.autonomous_pending = None
            return None
        step = acquisition_step_for_mode(
            target_step_id, decision.chosen_mode.value, len(self.meta_mode_observations(work_id, target_step_id)))
        acquisition = {"acquisition_id": 800 + len(self.acquisitions), "work_id": work_id,
                       "plan_id": plan_id, "target_step_id": target_step_id,
                       "rung": step.rung.value, "rung_index": step.rung_index,
                       "action_step_id": step.action_step_id, "instruction": step.instruction,
                       "proposer_only": False, "status": "pending"}
        self.acquisitions.append(acquisition)
        if self.autonomous_pending is not None:
            self.autonomous_pending.update(
                acquisition_id=acquisition["acquisition_id"], acquisition_rung=step.rung.value,
                acquisition_instruction=step.instruction, acquisition_expense=0,
                acquisition_blast=0, acquisition_reversible=True, acquisition_spends=False,
                acquisition_human=False, acquisition_commits=False)
        return acquisition


class MetaExecutor(PlannedExecutor):
    def __init__(self, db):
        super().__init__(db)
        self.meta_calls = 0

    @staticmethod
    def option(mode, direct, info, cost, instruction="acquire"):
        return {"mode": mode, "state": "bounded",
                "direct_value": {"low": direct[0], "high": direct[1]},
                "information_value": {"low": info[0], "high": info[1]},
                "cost": {"low": cost[0], "high": cost[1]},
                "evidence_refs": [f"mission:{mode}"], "rationale": f"forecast {mode}",
                "instruction": instruction, "expected_expense_usd": 0,
                "blast_radius_level": 0, "reversible": True, "spends_money": False,
                "touches_human": mode in {"human_question", "human_demonstration"},
                "commits": False, "block_reason": None,
                "wake_condition": "new evidence" if mode == "abstain" else None}

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.META_MODE_SCHEMA:
            self.meta_calls += 1
            if self.meta_calls == 1:
                # Internal computation is cheaper (cost 1 vs 3), but experiment has net 5 vs 1.
                options = [self.option("internal_computation", (2, 2), (0, 0), (1, 1), "think"),
                           self.option("environment_experiment", (4, 4), (4, 4), (3, 3), "Run a reversible tool/environment experiment; do not execute target"),
                           self.option("abstain", (0, 0), (0, 0), (0, 0), "")]
            else:
                # After the observation, every further acquisition has a negative BEST case.
                options = [self.option("internal_computation", (0, 1), (0, 0), (2, 2), "think"),
                           self.option("environment_experiment", (0, 1), (0, 1), (3, 3), "Run a reversible tool/environment experiment; do not execute target"),
                           self.option("abstain", (0, 0), (0, 0), (0, 0), "")]
            present = {option["mode"] for option in options}
            from runner.meta_mode import MetaMode
            for mode in MetaMode:
                if mode.value not in present:
                    options.append({"mode": mode.value, "state": "blocked",
                                    "direct_value": None, "information_value": None, "cost": None,
                                    "evidence_refs": [f"unavailable:{mode.value}"],
                                    "rationale": "unavailable in fixture", "instruction": "",
                                    "block_reason": "fixture unavailable", "wake_condition": None})
            return {"options": options}, Usage()
        return super().run(prompt, schema, tier)


def test_real_loop_boundary_compares_modes_then_explicitly_stops_after_observation():
    db = MetaDb()
    ex = MetaExecutor(db)
    loop = Loop(instance(), db, ex)

    first = loop.cycle()
    assert first is not None
    assert db.meta_decisions[0]["chosen_mode"] == "environment_experiment"
    assert db.meta_decisions[0]["chosen_score"] == "5"
    assert db.acquisitions[0]["rung"] == "environment_experiment"
    assert db.events.index("meta_decision_persisted") < db.events.index("act")
    assert "reversible tool/environment experiment" in ex.acted_prompt
    assert "WORK: collect and verify" not in ex.acted_prompt

    second = loop.cycle()
    assert second is None
    assert db.meta_decisions[1]["decision"] == "abstain"
    assert db.meta_decisions[1]["chosen_mode"] == "abstain"
    assert db.meta_decisions[1]["stop_reason"] == "no_option_worth_cost"
    assert ex.meta_calls == 2
    assert len(db.started) == 1  # STOP did not create an ACT run.


class FailOnceMetaExecutor(MetaExecutor):
    def __init__(self, db):
        super().__init__(db)
        self.fail_once = True

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.ACT_SCHEMA and self.fail_once:
            self.fail_once = False
            raise RuntimeError("injected ACT failure after durable meta decision")
        return super().run(prompt, schema, tier)


def test_pending_selected_mode_recovers_without_rescoring_after_act_failure():
    db = MetaDb()
    ex = FailOnceMetaExecutor(db)
    loop = Loop(instance(), db, ex)
    with pytest.raises(RuntimeError, match="injected ACT failure"):
        loop.cycle()
    assert ex.meta_calls == 1
    assert len(db.meta_decisions) == 1 and db.acquisitions[0]["status"] == "pending"

    recovered = Loop(instance(), db, ex).cycle()
    assert recovered is not None
    assert ex.meta_calls == 1  # exact durable selection resumed; no duplicate decision INSERT
    assert len(db.meta_decisions) == 1 and db.acquisitions[0]["status"] == "completed"


class PracticeMetaExecutor(MetaExecutor):
    def __init__(self, db, valid=True):
        super().__init__(db)
        self.valid = valid

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.META_MODE_SCHEMA:
            self.meta_calls += 1
            options = [self.option("autonomous_practice", (4, 4), (3, 3), (1, 1),
                                   "Practice in sandbox; do not touch the live target; run holdout transfer test"),
                       self.option("abstain", (0, 0), (0, 0), (0, 0), "")]
            present = {option["mode"] for option in options}
            from runner.meta_mode import MetaMode
            for mode in MetaMode:
                if mode.value not in present:
                    options.append({"mode": mode.value, "state": "blocked",
                                    "direct_value": None, "information_value": None, "cost": None,
                                    "evidence_refs": [f"unavailable:{mode.value}"],
                                    "rationale": "unavailable", "instruction": "",
                                    "block_reason": "unavailable", "wake_condition": None,
                                    "expected_expense_usd": 0, "blast_radius_level": 0,
                                    "reversible": True, "spends_money": False,
                                    "touches_human": False, "commits": False})
            return {"options": options}, Usage()
        if schema is prompts.ACT_SCHEMA:
            metrics = [{"metric": "practice_trials", "value": 3}]
            if self.valid:
                metrics.append({"metric": "holdout_transfer_score", "value": 0.8})
            return {"outcome": "sandbox practice complete", "succeeded": True,
                    "evidence": "sandbox:holdout-report", "observed_metrics": metrics,
                    "step_results": [{"step_id": "collect", "executed": False, "confirmed": False,
                                      "harmed_concern_ids": [], "evidence": "live target untouched"},
                                     {"step_id": "verify", "executed": False, "confirmed": False,
                                      "harmed_concern_ids": [], "evidence": "live target untouched"}],
                    "causal_proposals": []}, Usage()
        return super().run(prompt, schema, tier)


def test_autonomous_practice_requires_and_accepts_holdout_transfer_receipt():
    db = MetaDb(); cycle = Loop(instance(), db, PracticeMetaExecutor(db, valid=True)).cycle()
    assert cycle is not None and db.acquisitions[0]["rung"] == "autonomous_practice"
    assert db.acquisitions[0]["status"] == "completed"


def test_autonomous_practice_without_transfer_receipt_fails_loudly():
    db = MetaDb()
    with pytest.raises(RuntimeError, match="holdout transfer"):
        Loop(instance(), db, PracticeMetaExecutor(db, valid=False)).cycle()
    assert db.acquisitions[0]["status"] == "skipped"
    assert "acquisition_quarantined" in db.events


class RepeatExperimentExecutor(MetaExecutor):
    def run(self, prompt, schema, tier="working"):
        if schema is prompts.META_MODE_SCHEMA:
            self.meta_calls += 1
            if self.meta_calls <= 2:
                options = [self.option("environment_experiment", (4, 4), (3, 3), (2, 2),
                                       f"repeatable probe {self.meta_calls}; do not execute target"),
                           self.option("abstain", (0, 0), (0, 0), (0, 0), "")]
            else:
                options = [self.option("environment_experiment", (0, 0), (0, 0), (2, 2), "probe"),
                           self.option("abstain", (0, 0), (0, 0), (0, 0), "")]
            present = {option["mode"] for option in options}
            from runner.meta_mode import MetaMode
            for mode in MetaMode:
                if mode.value not in present:
                    options.append({"mode": mode.value, "state": "blocked", "direct_value": None,
                                    "information_value": None, "cost": None,
                                    "evidence_refs": [f"unavailable:{mode.value}"],
                                    "rationale": "unavailable", "instruction": "",
                                    "expected_expense_usd": 0, "blast_radius_level": 0,
                                    "reversible": True, "spends_money": False,
                                    "touches_human": False, "commits": False,
                                    "block_reason": "unavailable", "wake_condition": None})
            return {"options": options}, Usage()
        return super().run(prompt, schema, tier)


def test_same_channel_can_be_optimal_again_after_an_observation():
    db = MetaDb(); ex = RepeatExperimentExecutor(db); loop = Loop(instance(), db, ex)
    assert loop.cycle() is not None
    assert loop.cycle() is not None
    assert [a["rung"] for a in db.acquisitions] == ["environment_experiment", "environment_experiment"]
    assert [d["chosen_mode"] for d in db.meta_decisions] == ["environment_experiment", "environment_experiment"]


class InvalidSemanticMetaExecutor(MetaExecutor):
    def run(self, prompt, schema, tier="working"):
        if schema is prompts.META_MODE_SCHEMA:
            self.meta_calls += 1
            duplicate = self.option("abstain", (0, 0), (0, 0), (0, 0), "")
            return {"options": [duplicate, dict(duplicate)]}, Usage(tokens_in=13, tokens_out=5)
        return super().run(prompt, schema, tier)


def test_semantically_invalid_forecast_is_accounted_then_parked_not_retried():
    db = MetaDb()
    loop = Loop(instance(), db, InvalidSemanticMetaExecutor(db))
    with pytest.raises(ValueError, match="unique modes"):
        loop.cycle()
    assert len(db.meta_attempts) == 1
    assert db.meta_attempts[0]["usage"].tokens_in == 13
    assert db.rejected_attempts and "unique modes" in db.rejected_attempts[0][1]
    assert db.autonomous_pending is None


def test_forecast_hard_cap_is_rechecked_after_persistence_before_act():
    db = MetaDb()
    loop = Loop(instance(), db, MetaExecutor(db))
    original = loop.budget.check_hard_cap
    def checked():
        db.events.append("hard_cap_check")
        return original()
    loop.budget.check_hard_cap = checked
    loop.cycle()
    decision_i = db.events.index("meta_decision_persisted")
    act_i = db.events.index("act")
    assert any(event == "hard_cap_check" for event in db.events[decision_i + 1:act_i])


def test_retired_goal_relaxation_proposal_is_not_wakeable():
    db = object.__new__(Db)
    captured = {}
    def q(sql, args=(), one=False):
        captured["sql"] = sql
        return []
    db._q = q
    assert db.wake_impasse_stops() == 0
    assert "w.kind <> 'goal_relaxation_proposal'" in captured["sql"]
