#!/usr/bin/env python3
"""Real PostgreSQL + Loop proof of compare-not-order and explicit post-observation stop.

The executor is deterministic and subscription-shaped: this proof exercises the production Loop,
Db, transactions, and evaluator against a separately migrated database without a metered call.
"""
from __future__ import annotations

import json, os, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner import prompts
from runner.config import (Autonomy, Boundaries, BudgetCfg, Curiosity, Engine, Instance,
                           Measure, Mission, Models, Money, Surfaces)
from runner.db import Db
from runner.executor import Usage
from runner.loop import Loop


class Executor:
    def __init__(self, db, prefix):
        self.db, self.prefix = db, prefix
        self.meta_calls = 0
        self.fail_first_act = os.environ.get("AQ_PROOF_FAIL_FIRST_ACT") == "1"

    @staticmethod
    def option(mode, direct, info, cost, instruction="acquire"):
        return {"mode": mode, "state": "bounded",
                "direct_value": {"low": direct[0], "high": direct[1]},
                "information_value": {"low": info[0], "high": info[1]},
                "cost": {"low": cost[0], "high": cost[1]},
                "evidence_refs": [f"real-db-forecast:{mode}"],
                "rationale": f"bounded forecast for {mode}", "instruction": instruction,
                "expected_expense_usd": 0, "blast_radius_level": 0, "reversible": True,
                "spends_money": False,
                "touches_human": mode in {"human_question", "human_demonstration"},
                "commits": False, "block_reason": None,
                "wake_condition": "new mission or causal evidence" if mode == "abstain" else None}

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.DECIDE_SCHEMA:
            step = {"step_id": "unsupported", "subgoal_id": "goal",
                    "action": f"{self.prefix}-target", "expected_effect": "mission improves",
                    "expected_direction": "toward", "scope": {},
                    "blast_radius": {"affected_entities_upper_bound": 0,
                                     "public_or_unbounded": False, "production_wide": False,
                                     "irreversible_external_write": False}}
            concerns = [
                {"concern_id": "serve_mission_progress", "kind": "serve",
                 "predicate": {"metric": "mission_delta", "operator": ">=", "value": 0}},
                {"concern_id": "must_not_overshoot", "kind": "must_not_harm",
                 "predicate": {"metric": "mission_value", "operator": "<=", "value": 1}},
            ]
            plan = {"goal_predicate": concerns[0]["predicate"], "expected_expense_usd": 0,
                    "mission_concerns": concerns,
                    "subgoals": [
                        {"subgoal_id": "goal", "success_predicate": concerns[0]["predicate"],
                         "serves_concern_ids": ["serve_mission_progress"]},
                        {"subgoal_id": "bounded", "success_predicate": concerns[1]["predicate"],
                         "serves_concern_ids": ["must_not_overshoot"]}], "steps": [step]}
            return {"do_nothing": False, "kind": "impasse-proof", "summary": "unsupported target",
                    "rationale": "force a localized plan impasse", "reversible": True,
                    "spends_money": False, "touches_human": False, "commits": False,
                    "plan": plan}, Usage()
        if schema is prompts.META_MODE_SCHEMA:
            self.meta_calls += 1
            if self.meta_calls == 1:
                # Cheapest compute: cost 1, net 1. Experiment: cost 3, net 5.
                options = [self.option("internal_computation", (2,2), (0,0), (1,1), "think"),
                           self.option("environment_experiment", (4,4), (4,4), (3,3), "Run a reversible tool/environment experiment; do not execute target"),
                           self.option("human_question", (1,1), (2,2), (1,1), "ask"),
                           self.option("human_demonstration", (1,2), (1,2), (2,2), "demonstrate"),
                           self.option("autonomous_practice", (2,3), (2,2), (1,2), "practice"),
                           self.option("goal_relaxation", (1,1), (0,0), (2,2), "relax"),
                           self.option("abstain", (0,0), (0,0), (0,0), "")]
                experiment = next(o for o in options if o["mode"] == "environment_experiment")
                experiment.update(expected_expense_usd=1.25, blast_radius_level=1,
                                  reversible=True, spends_money=True)
            elif self.meta_calls == 2 and os.environ.get("AQ_PROOF_REPEAT_EXPENSE") == "1":
                # A genuinely new observation can justify the same channel again; reserve its
                # external expense under its own decision rather than the parent work id.
                options = [self.option("environment_experiment", (4,4), (3,3), (3,3),
                                       "Run a second distinct bounded probe; do not execute target"),
                           self.option("abstain", (0,0), (0,0), (0,0), "")]
                present = {o["mode"] for o in options}
                for mode in ("internal_computation", "human_question", "human_demonstration",
                             "autonomous_practice", "goal_relaxation"):
                    if mode not in present:
                        options.append({"mode": mode, "state": "blocked", "direct_value": None,
                                        "information_value": None, "cost": None,
                                        "evidence_refs": ["repeat-control"], "rationale": "blocked in control",
                                        "instruction": "", "block_reason": "control isolation",
                                        "wake_condition": None, "expected_expense_usd": 0,
                                        "blast_radius_level": 0, "reversible": True,
                                        "spends_money": False, "touches_human": False, "commits": False})
                repeat = next(o for o in options if o["mode"] == "environment_experiment")
                repeat.update(expected_expense_usd=2.50, blast_radius_level=1,
                              reversible=True, spends_money=True)
            else:
                # Observation made every remaining channel known-worse than stopping.
                options = [self.option("internal_computation", (0,1), (0,0), (2,2), "think"),
                           self.option("environment_experiment", (0,1), (0,1), (3,3), "Run a reversible tool/environment experiment; do not execute target"),
                           self.option("human_question", (0,1), (0,1), (3,3), "ask"),
                           self.option("human_demonstration", (0,1), (0,1), (3,4), "demonstrate"),
                           self.option("autonomous_practice", (0,1), (0,1), (3,4), "practice"),
                           self.option("goal_relaxation", (0,1), (0,0), (2,2), "relax"),
                           self.option("abstain", (0,0), (0,0), (0,0), "")]
            return {"options": options}, Usage(tokens_in=11, tokens_out=7)
        if schema is prompts.ACT_SCHEMA:
            if self.fail_first_act:
                self.fail_first_act = False
                raise RuntimeError("injected post-selection ACT failure")
            assert "reversible tool/environment experiment" in prompt
            self.db._q(f"INSERT INTO {self.prefix}_observations(note) VALUES('bounded probe: no support')")
            return {"outcome": "bounded probe found no decision-supporting relation",
                    "succeeded": True, "evidence": f"table:{self.prefix}_observations",
                    "observed_metrics": [{"metric": "mission_delta", "value": 0}],
                    "step_results": [{"step_id": "unsupported", "executed": False,
                                      "confirmed": False, "evidence": "target not executed",
                                      "harmed_concern_ids": []}], "causal_proposals": []}, Usage()
        if schema is prompts.REFLECT_SCHEMA:
            return {"insight": "the bounded probe did not justify more acquisition",
                    "evidence": f"table:{self.prefix}_observations", "scope": "local",
                    "confidence": 0.9}, Usage()
        raise AssertionError(schema)


def main():
    os.environ["AQ_CAUSAL_AUTOMINE"] = "0"
    db = Db(os.environ.get("AQ_DB_URL", "postgresql://kthomas@localhost/aq_voi_test"), graph="none")
    prefix = "voi_" + uuid.uuid4().hex[:10]
    db._q(f"CREATE TABLE {prefix}_metric(value integer NOT NULL)")
    db._q(f"INSERT INTO {prefix}_metric VALUES(0)")
    db._q(f"CREATE TABLE {prefix}_observations(note text NOT NULL)")
    work_id = None
    try:
        inst = Instance(Mission("resolve a real plan impasse by net value",
                    Measure("proof value", f"SELECT value FROM {prefix}_metric", target=1),
                    "two observations", Boundaries()),
                Engine(mode="subscription"), "proof", {"graph": "none"}, Models(),
                BudgetCfg(money=Money(0, 50), autonomy=Autonomy()),
                Surfaces(notify_channel="none"), Curiosity(enabled=False))
        ex = Executor(db, prefix); loop = Loop(inst, db, ex)
        if os.environ.get("AQ_PROOF_FAIL_PENDING_WINDOW") == "1":
            # THE PENDING WINDOW — the crash window AQ_PROOF_FAIL_FIRST_ACT cannot reach.
            #
            # AQ_PROOF_FAIL_FIRST_ACT injects at the ACT call, which happens AFTER
            # mark_acquisition_running() has already moved the row pending -> running. So the row
            # is always 'running' at that crash point, and a recovery join written as
            # `pa.status='running'` finds it. That control passes on BOTH the correct and the
            # defective query — it cannot come back negative for the defect that actually stopped
            # production.
            #
            # This control crashes one step earlier: after the acquisition row exists (status
            # DEFAULTS to 'pending') and before the transition. Recovery must still see it. Under
            # the defective join the acquisition is invisible, recovery concludes the row is
            # ordinary work, tries work=done, and PostgreSQL refuses with
            #     work <id> cannot be done while acquisition is open
            # which is the live failure reproduced in BB #2608.
            #
            # MEASURED SCOPE — DO NOT OVERSTATE THIS CONTROL.
            #
            # I ran the negative control: reverted pending_reflection to `pa.status='running'`,
            # reset the Compose volume, re-ran. IT STILL PASSED, exit 0. So this block does NOT
            # cover the L1 defect, and any comment claiming it does would be false.
            #
            # WHY: crashing at mark_acquisition_running happens before a run row exists, so
            # recovery is handled by the ORPHANED-RUN reconciler ("run N was orphaned (started,
            # never recorded)") and pending_reflection's join is never consulted. Same path on
            # both the fixed and defective query.
            #
            # WHAT IT DOES COVER (verified): a crash between acquisition creation and its
            # pending->running transition is recovered to a completed end state rather than
            # wedging. That is a real, previously untested window — just not L1's.
            #
            # L1's behavioural case is still UNCOVERED. To reach it the injection must leave a run
            # with outcome IS NOT NULL and completed_at IS NULL while an acquisition on the same
            # work is still 'pending' — the state reproduced live in BB #2608. Until such a case
            # exists, the only L1 guard is the static assertion in
            # tests/test_pending_reflection_l1.py, which fails on revert but proves nothing about
            # runtime behaviour.
            real_mark = db.mark_acquisition_running
            state = {"fired": False}

            def crash_in_pending_window(acquisition_id):
                if not state["fired"]:
                    state["fired"] = True
                    raise RuntimeError("injected pending-window failure")
                return real_mark(acquisition_id)

            db.mark_acquisition_running = crash_in_pending_window
            try:
                loop.cycle()
                raise AssertionError("injected pending-window failure did not fire")
            except RuntimeError as exc:
                assert "injected pending-window failure" in str(exc), exc
            db.mark_acquisition_running = real_mark
            assert state["fired"], "crash hook never ran — the seam moved, this control is inert"
            open_rows = db._q(
                "SELECT acquisition_id, status FROM plan_acquisition WHERE status='pending'") or []
            assert open_rows, "no acquisition left in the pending window — nothing to recover from"
            # A fresh Loop must recover it. This is the assertion that fails on the defect.
            loop = Loop(inst, db, ex)
        if ex.fail_first_act:
            try:
                loop.cycle()
                raise AssertionError("injected ACT failure did not fire")
            except RuntimeError as exc:
                assert "injected post-selection ACT failure" in str(exc)
            # New Loop object recovers the exact pending selected acquisition without rescoring.
            loop = Loop(inst, db, ex)
        first = loop.cycle(); assert first is not None; work_id = first.work_id
        second = loop.cycle()
        if os.environ.get("AQ_PROOF_REPEAT_EXPENSE") == "1":
            assert second is not None
            third = loop.cycle(); assert third is None
        else:
            assert second is None
        decisions = db._q(
            "SELECT observation_index,policy_version,decision,chosen_mode,chosen_score,stop_reason,scorecards,tokens_in,tokens_out,cost_usd,expected_expense_usd,blast_radius_level,spends_money "
            "FROM impasse_meta_mode_decision WHERE work_id=%s ORDER BY observation_index", (work_id,))
        acquisitions = db._q(
            "SELECT rung,status,instruction,result FROM plan_acquisition WHERE work_id=%s ORDER BY acquisition_id",
            (work_id,))
        attempts = db._q(
            "SELECT attempt_id,decision_id,validation_error,tokens_in,tokens_out,cost_usd "
            "FROM impasse_meta_mode_forecast_attempt WHERE work_id=%s ORDER BY attempt_id", (work_id,))
        reservations = db._q(
            "SELECT decision_id,expected_expense_usd,status FROM meta_mode_spend_reservation "
            "WHERE decision_id IN (SELECT decision_id FROM impasse_meta_mode_decision WHERE work_id=%s) "
            "ORDER BY decision_id", (work_id,))
        ordering = db._q(
            "SELECT d.created_at < r.started_at AS decision_before_act, p.asserted_at < r.started_at AS prediction_before_act "
            "FROM impasse_meta_mode_decision d JOIN plan_acquisition a ON a.meta_mode_decision_id=d.decision_id "
            "JOIN runs r ON r.work_id=d.work_id JOIN planning_prediction p ON p.work_id=d.work_id "
            "AND p.step_id=a.action_step_id WHERE d.work_id=%s", (work_id,), one=True)
        output = {"work_id": work_id, "meta_calls": ex.meta_calls,
                  "recovered_after_injected_failure": os.environ.get("AQ_PROOF_FAIL_FIRST_ACT") == "1",
                  "decisions": [dict(x) for x in decisions],
                  "attempts": [dict(x) for x in attempts],
                  "reservations": [dict(x) for x in reservations],
                  "acquisitions": [dict(x) for x in acquisitions], "ordering": dict(ordering)}
        print(json.dumps(output, sort_keys=True, default=str))
        repeat_expense = os.environ.get("AQ_PROOF_REPEAT_EXPENSE") == "1"
        assert [d["decision"] for d in decisions] == (
            ["acquire", "acquire", "abstain"] if repeat_expense else ["acquire", "abstain"])
        assert len(attempts) == len(decisions)
        assert all(a["decision_id"] is not None and a["validation_error"] is None for a in attempts)
        assert decisions[0]["chosen_mode"] == "environment_experiment"
        assert str(decisions[0]["chosen_score"]) == "5"
        stop = decisions[-1]
        assert stop["chosen_mode"] == "abstain"
        assert stop["stop_reason"] == "no_option_worth_cost"
        assert all(d["tokens_in"] == 11 and d["tokens_out"] == 7 for d in decisions)
        assert str(decisions[0]["expected_expense_usd"]) == "1.2500"
        assert decisions[0]["blast_radius_level"] == 1 and decisions[0]["spends_money"] is True
        assert len(acquisitions) == (2 if repeat_expense else 1)
        assert all(a["status"] == "completed" for a in acquisitions)
        # DONE-WHILE-OPEN INVARIANT. Asserted as a property of the database, not of this run's
        # bookkeeping: no work may be terminal while any acquisition against it is still open.
        # The database refuses this at write time; a violation here means something wrote around
        # the guard. Enumerates the OPEN states rather than excluding terminal ones, so a newly
        # added status shows up as a loud failure instead of being silently treated as closed.
        contradiction = db._q(
            "SELECT w.id, w.status AS work_status, pa.acquisition_id, pa.status AS acq_status "
            "FROM work w JOIN plan_acquisition pa ON pa.work_id=w.id "
            "WHERE w.status IN ('done','abandoned') AND pa.status IN ('pending','running')") or []
        assert not contradiction, f"work marked terminal with an OPEN acquisition: {contradiction}"
        if os.environ.get("AQ_PROOF_FAIL_PENDING_WINDOW") == "1":
            # Recovery from the pending window must reach the SAME completed end state, not merely
            # avoid crashing. "It did not raise" is not evidence that recovery worked.
            assert all(a["status"] == "completed" for a in acquisitions), acquisitions
        assert all("Do not execute the target action broadly." in a["instruction"]
                   and "FORECAST-SPECIFIC DETAIL" in a["instruction"] for a in acquisitions)
        assert [str(r["expected_expense_usd"]) for r in reservations] == (
            ["1.250000", "2.500000"] if repeat_expense else ["1.250000"])
        assert all(r["status"] == "incurred" for r in reservations)
        assert ordering["decision_before_act"] and ordering["prediction_before_act"]
        assert db._q(f"SELECT count(*) AS n FROM {prefix}_observations", one=True)["n"] == (2 if repeat_expense else 1)
        assert db._q(f"SELECT value FROM {prefix}_metric", one=True)["value"] == 0
        # STOP is not a permanent graveyard: a newer, matching, completed-run-backed causal
        # observation wakes the exact abandoned work. Remove the synthetic edge after proving it.
        evidence_run = db._q(
            "SELECT id FROM runs WHERE work_id=%s AND completed_at IS NOT NULL "
            "ORDER BY completed_at DESC LIMIT 1", (work_id,), one=True)["id"]
        edge = db._q(
            "INSERT INTO causal_edge(source_action,direct_effect,mission_measure,scope_conditions,"
            " evidence_run_ids) VALUES(%s,'mission improves','proof value','{}',%s) "
            "RETURNING edge_id", (f"{prefix}-target", [evidence_run]), one=True)
        wake_count = db.wake_impasse_stops()
        assert wake_count == 1
        assert db._q("SELECT status FROM work WHERE id=%s", (work_id,), one=True)["status"] == "pending"
        db._q("DELETE FROM causal_edge WHERE edge_id=%s", (edge["edge_id"],))
    finally:
        if work_id: db._q("DELETE FROM work WHERE id=%s", (work_id,))
        db._q(f"DROP TABLE IF EXISTS {prefix}_observations")
        db._q(f"DROP TABLE IF EXISTS {prefix}_metric")

if __name__ == "__main__": main()
