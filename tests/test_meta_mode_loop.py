from runner import prompts
from runner.acquisition import acquisition_step_for_mode
from runner.executor import Usage
from runner.loop import Loop
from tests.test_loop_approval_execution import FakeDb, instance
from tests.test_pre_act_predictions import PlannedExecutor


class MetaDb(FakeDb):
    def __init__(self):
        super().__init__()
        self.meta_decisions = []

    def meta_mode_observations(self, work_id, target_step_id):
        return [{"acquisition_id": a["acquisition_id"], "rung": a["rung"],
                 "result": a.get("result")}
                for a in self.acquisitions if a["work_id"] == work_id
                and a["target_step_id"] == target_step_id and a["status"] == "completed"]

    def prepare_meta_mode_decision(self, work_id, plan_id, target_step_id, decision):
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
                "instruction": instruction, "block_reason": None,
                "wake_condition": "new evidence" if mode == "abstain" else None}

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.META_MODE_SCHEMA:
            self.meta_calls += 1
            if self.meta_calls == 1:
                # Internal computation is cheaper (cost 1 vs 3), but experiment has net 5 vs 1.
                options = [self.option("internal_computation", (2, 2), (0, 0), (1, 1), "think"),
                           self.option("environment_experiment", (4, 4), (4, 4), (3, 3), "probe"),
                           self.option("abstain", (0, 0), (0, 0), (0, 0), "")]
            else:
                # After the observation, every further acquisition has a negative BEST case.
                options = [self.option("internal_computation", (0, 1), (0, 0), (2, 2), "think"),
                           self.option("environment_experiment", (0, 1), (0, 1), (3, 3), "probe"),
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
