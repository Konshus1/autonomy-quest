#!/usr/bin/env python3
"""Exact-Compose proof that a provisional causal proposal cannot affect plan sufficiency.

This is a deterministic, subscription-shaped executor driving the production Loop, Db, budget,
intent verifier, direction-first sufficiency check, acquisition router, evaluator, and migrated
PostgreSQL schema.  No model/API call occurs.

Run each arm against a FRESH Compose project/volume.  The frozen DECIDE payload is byte-identical
in all arms; only the database principle state differs:

* baseline: no candidate edge
* provisional: the exact contradictory edge exists but its latest transition is provisional
* active: the same edge has two cross-environment supports and an authorized promotion

Exit 0 means the arm produced its required receipt.  Exit 1 means the target provisional-inertness
control went red.  Exit 2 means the rig/preflight failed (including an active arm that cannot
influence the production decision path).  A skip is never an outcome.

IMPORTANT: docker-compose BUILDS this file into the image.  Rebuild before every control you intend
to believe.  A source edit followed by ``compose run`` without ``--build`` tests stale code.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner import prompts
from runner.config import (
    Autonomy, Boundaries, BudgetCfg, Curiosity, Engine, Instance, Measure, Mission,
    Models, Money, Surfaces,
)
from runner.db import Db
from runner.executor import Usage
from runner.loop import Loop

ACTION = "inert-proposal-control:publish-blocked-step"
EFFECT = "public release becomes available"
SUMMARY = "INERT_PROPOSAL_CONTROL frozen target decision"
EVIDENCE_SUMMARY = "INERT_PROPOSAL_CONTROL completed evidence fixture"
SCOPE = "{}"


def _probe() -> dict:
    goal = {"metric": "mission_delta", "operator": ">=", "value": 0}
    bounded = {"metric": "mission_value", "operator": "<=", "value": 1}
    return {
        "do_nothing": False,
        "kind": "inert-proposal-proof",
        "summary": SUMMARY,
        "rationale": "exercise the exact candidate relation without granting it authority",
        "reversible": True,
        "spends_money": False,
        "touches_human": False,
        "commits": False,
        "plan": {
            "goal_predicate": goal,
            "expected_expense_usd": 0,
            "mission_concerns": [
                {"concern_id": "serve_mission_progress", "kind": "serve", "predicate": goal},
                {"concern_id": "must_not_overshoot", "kind": "must_not_harm", "predicate": bounded},
            ],
            "subgoals": [
                {"subgoal_id": "goal", "success_predicate": goal,
                 "serves_concern_ids": ["serve_mission_progress"]},
                {"subgoal_id": "bounded", "success_predicate": bounded,
                 "serves_concern_ids": ["must_not_overshoot"]},
            ],
            "steps": [{
                "step_id": "frozen-step",
                "subgoal_id": "goal",
                "action": ACTION,
                "expected_effect": EFFECT,
                "expected_direction": "toward",
                "scope": {},
                "blast_radius": {
                    "affected_entities_upper_bound": 0,
                    "public_or_unbounded": False,
                    "production_wide": False,
                    "irreversible_external_write": False,
                },
            }],
        },
    }


PROBE = _probe()
PROBE_BYTES = json.dumps(PROBE, sort_keys=True, separators=(",", ":")).encode()
PROBE_SHA256 = hashlib.sha256(PROBE_BYTES).hexdigest()


class RigFailure(RuntimeError):
    pass


class TargetControlRed(AssertionError):
    pass


class Executor:
    """Deterministic subscription-shaped executor; production Loop still owns all decisions."""

    def __init__(self, db: Db, metric_table: str):
        self.db = db
        self.metric_table = metric_table
        self.action_calls: list[str] = []

    @staticmethod
    def _option(mode: str, state: str = "bounded") -> dict:
        bounded = state == "bounded"
        return {
            "mode": mode,
            "state": state,
            "direct_value": {"low": 1, "high": 1} if bounded else None,
            "information_value": {"low": 2, "high": 2} if bounded else None,
            "cost": {"low": 1, "high": 1} if bounded else None,
            "evidence_refs": [f"inert-control:{mode}"],
            "rationale": "bounded contradictory-relation probe" if bounded else "not selected",
            "instruction": ("Run a reversible tool/environment experiment; do not execute target"
                            if bounded else ""),
            "expected_expense_usd": 0,
            "blast_radius_level": 0,
            "reversible": True,
            "spends_money": False,
            "touches_human": False,
            "commits": False,
            "block_reason": None if bounded else "control isolation",
            "wake_condition": None,
        }

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.DECIDE_SCHEMA:
            # Return a deep copy so the exact frozen bytes cannot be mutated between phases.
            return copy.deepcopy(PROBE), Usage()
        if schema is prompts.META_MODE_SCHEMA:
            options = [self._option("environment_experiment")]
            for mode in (
                "internal_computation", "human_question", "human_demonstration",
                "autonomous_practice", "goal_relaxation", "abstain",
            ):
                options.append(self._option(mode, "blocked"))
            return {"options": options}, Usage()
        if schema is prompts.ACT_SCHEMA:
            # Only the missing-relation arm may reach ACT, and it must be the bounded acquisition
            # action, never the frozen target action.
            acquisition = "Do not execute the target action broadly." in prompt
            label = "bounded-acquisition" if acquisition else "frozen-target"
            self.action_calls.append(label)
            self.db._q(
                f"INSERT INTO {self.metric_table}_observations(note) VALUES(%s)",
                (label,),
            )
            return {
                "outcome": f"observed {label}",
                "succeeded": True,
                "evidence": f"table:{self.metric_table}_observations",
                "observed_metrics": [{"metric": "mission_delta", "value": 0}],
                "step_results": [{
                    "step_id": "frozen-step", "executed": False, "confirmed": False,
                    "evidence": f"{label}; frozen target not executed", "harmed_concern_ids": [],
                }],
                "causal_proposals": [],
            }, Usage()
        if schema is prompts.REFLECT_SCHEMA:
            return {
                "insight": "bounded acquisition did not authorize the candidate relation",
                "evidence": f"table:{self.metric_table}_observations",
                "scope": "local", "confidence": 0.9,
            }, Usage()
        raise RigFailure(f"unexpected schema {schema!r}")


def _seed_completed_evidence(db: Db) -> int:
    work_id = db.create_work(
        "proof-fixture", EVIDENCE_SUMMARY, "completed evidence required by causal_edge",
        reversible=True, spends_money=False, touches_human=False, commits=False,
        expected_expense_usd=0, blast_radius_level=0,
    )
    run_id = db.start_run(work_id)
    with db.tx() as cur:
        db.complete_run(
            cur, run_id, outcome="fixture completed", succeeded=True, usage=Usage(),
            productive=True, evidence="inert-control:evidence", measure_before=0, measure_after=0,
        )
        db.write_learning(
            cur, run_id, "fixture establishes completed-run provenance",
            "inert-control:evidence", "local", 0.99,
        )
    return run_id


def _seed_wake_fixture(db: Db) -> int:
    """Create a stopped impasse whose exact missing edge may later wake it."""
    wake_plan = copy.deepcopy(PROBE["plan"])
    work_id = db.create_work(
        "wake-proof", "INERT_PROPOSAL_CONTROL stopped wake fixture",
        "stopped until an authoritative relation exists", plan_id="wake-proof-plan",
        plan=wake_plan, reversible=True, spends_money=False, touches_human=False, commits=False,
        expected_expense_usd=0, blast_radius_level=0,
    )
    db._q("UPDATE work SET status='abandoned' WHERE id=%s", (work_id,))
    db._q(
        "INSERT INTO impasse_meta_mode_decision("
        "work_id,plan_id,target_step_id,observation_index,policy_version,scorecards,decision,"
        "stop_reason,expected_expense_usd,blast_radius_level,reversible,spends_money,"
        "touches_human,commits,tokens_in,tokens_out,cost_usd) "
        "VALUES(%s,'wake-proof-plan','frozen-step',0,'inert-control-v1','[]'::jsonb,'stop',"
        "'no authoritative direction',0,0,true,false,false,false,0,0,0)",
        (work_id,),
    )
    return work_id


def _insert_provisional(db: Db, evidence_run_id: int) -> int:
    row = db._q(
        "INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,"
        "mechanism_description,scope_conditions,predicted_certainty,evidence_run_ids,support_count) "
        "VALUES(%s,%s,'proof metric','away','contradicts the frozen toward step',%s,0.95,%s,0) "
        "RETURNING edge_id",
        (ACTION, EFFECT, SCOPE, [evidence_run_id]), one=True,
    )
    latest = db._q(
        "SELECT to_status,authority_after,transition_kind FROM causal_principle_transition "
        "WHERE cause=%s AND effect=%s AND scope=%s ORDER BY id DESC LIMIT 1",
        (ACTION, EFFECT, SCOPE), one=True,
    )
    if not latest or latest["to_status"] != "provisional" or latest["authority_after"]:
        raise RigFailure(f"proposal did not enter provisional/inert state: {latest}")
    return int(row["edge_id"])


def _promote(db: Db) -> int:
    common = (ACTION, EFFECT, SCOPE)
    for idx, domain in enumerate(("control-domain-a", "control-domain-b"), start=1):
        db._q(
            "INSERT INTO causal_principle_transition("
            "cause,effect,scope,from_status,to_status,transition_kind,environment_id,"
            "environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,"
            "evidence_result,bounded_experiment,authority_after,transitioned_by,rule_version,"
            "automatic,detail) VALUES(%s,%s,%s,'provisional','provisional','shadow_test',%s,%s,%s,"
            "'inert-control-mission','inert-control-v1',%s,'supports',true,false,"
            "'independent-control-observer','aq-governed-principle-v1',false,'{}'::jsonb)",
            common + (f"control-execution-{idx}", domain, f"fingerprint-{idx}",
                      f"inert-control:shadow-{idx}"),
        )
    row = db._q(
        "INSERT INTO causal_principle_transition("
        "cause,effect,scope,from_status,to_status,transition_kind,environment_id,"
        "environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,"
        "evidence_result,bounded_experiment,authority_after,transitioned_by,adjudicated_by,"
        "negative_control,negative_control_result,rule_version,automatic,detail) "
        "VALUES(%s,%s,%s,'provisional','promoted','promote','control-promotion',"
        "'control-adjudication','control-promotion-fingerprint','inert-control-mission',"
        "'inert-control-v1','inert-control:promotion','authorized',false,true,"
        "'control-operator','independent-control-adjudicator','same frozen probe under provisional',"
        "'provisional matched baseline','aq-governed-principle-v1',false,"
        "'{\"applies_here\":\"true\",\"applies_here_how\":\"exact action/effect/scope\"}'::jsonb) "
        "RETURNING id",
        common, one=True,
    )
    return int(row["id"])


def _receipt(db: Db, arm: str, edge_id: int | None, result, ex: Executor,
             wake_receipt: dict) -> dict:
    work = db._q(
        "SELECT id,status,kind,summary,rationale,plan FROM work WHERE summary=%s ORDER BY id DESC LIMIT 1",
        (SUMMARY,), one=True,
    )
    if not work:
        raise RigFailure("production Loop did not persist the frozen decision")
    predictions = [dict(x) for x in (db._q(
        "SELECT step_id,edge_id,edge_state,sufficient,expected_direction,assessment_annotation "
        "FROM planning_prediction WHERE work_id=%s ORDER BY step_index", (work["id"],)
    ) or [])]
    acquisitions = [dict(x) for x in (db._q(
        "SELECT rung,status,target_step_id,action_step_id FROM plan_acquisition "
        "WHERE work_id=%s ORDER BY acquisition_id", (work["id"],)
    ) or [])]
    runs = [dict(x) for x in (db._q(
        "SELECT id,completed_at IS NOT NULL AS completed,succeeded,outcome FROM runs "
        "WHERE work_id=%s ORDER BY id", (work["id"],)
    ) or [])]
    latest = db._q(
        "SELECT to_status,authority_after,transition_kind FROM causal_principle_transition "
        "WHERE cause=%s AND effect=%s AND scope=%s ORDER BY id DESC LIMIT 1",
        (ACTION, EFFECT, SCOPE), one=True,
    ) if edge_id is not None else None
    return {
        "arm": arm,
        "probe_sha256": PROBE_SHA256,
        "probe_bytes": len(PROBE_BYTES),
        "edge_id": edge_id,
        "principle": dict(latest) if latest else None,
        "cycle_returned": result is not None,
        "work": {k: work[k] for k in ("id", "status", "kind", "summary", "rationale")},
        "predictions": predictions,
        "acquisitions": acquisitions,
        "runs": runs,
        "observed_action_calls": ex.action_calls,
        "wake": wake_receipt,
    }


def _validate(receipt: dict) -> None:
    arm = receipt["arm"]
    predictions = receipt["predictions"]
    target_predictions = [row for row in predictions if row["step_id"] == "frozen-step"]
    if len(target_predictions) != 1:
        raise RigFailure(f"frozen decision was not assessed exactly once: {predictions}")
    p = target_predictions[0]
    missing_path = (
        p["edge_id"] is None and p["edge_state"] == "absent" and not p["sufficient"]
        and len(receipt["acquisitions"]) == 1
        and receipt["acquisitions"][0]["target_step_id"] == "frozen-step"
        and len(receipt["runs"]) == 1 and receipt["runs"][0]["completed"]
        and receipt["observed_action_calls"] == ["bounded-acquisition"]
        and receipt["cycle_returned"]
    )
    blocked_path = (
        p["edge_id"] == receipt["edge_id"] and p["edge_state"] == "absent"
        and not p["sufficient"] and not receipt["acquisitions"] and not receipt["runs"]
        and not receipt["observed_action_calls"] and not receipt["cycle_returned"]
        and receipt["work"]["status"] == "abandoned"
        and "HARD PLAN CONTRADICTION" in receipt["work"]["rationale"]
    )
    wake = receipt["wake"]
    inert_wake = {
        "count": 0,
        "future_edge_stop_status": "abandoned",
        "existing_edge_stop_status": "abandoned",
    }
    active_wake = {
        "count": 2,
        "future_edge_stop_status": "pending",
        "existing_edge_stop_status": "pending",
    }
    if arm == "baseline":
        if (receipt["principle"] is not None or not missing_path
                or wake != {"provisional_phase": inert_wake, "active_phase": None}):
            raise RigFailure(f"baseline did not exercise the missing-relation acquisition path: {receipt}")
    elif arm == "active":
        principle = receipt["principle"] or {}
        if principle.get("to_status") != "promoted" or not principle.get("authority_after"):
            raise RigFailure(f"red-capable arm did not reach active status: {principle}")
        if (not blocked_path
                or wake != {"provisional_phase": inert_wake, "active_phase": active_wake}):
            raise RigFailure(
                "active fixture did not change both production consumers at the authority event; "
                f"control cannot establish inertness: {receipt}"
            )
    elif arm == "provisional":
        principle = receipt["principle"] or {}
        if principle.get("to_status") != "provisional" or principle.get("authority_after"):
            raise RigFailure(f"control arm was not provisional: {principle}")
        if (not missing_path
                or wake != {"provisional_phase": inert_wake, "active_phase": None}):
            raise TargetControlRed(
                "provisional proposal changed a production planning consumer instead of matching baseline: "
                f"{receipt}"
            )
    else:
        raise RigFailure(f"unknown arm {arm!r}")


def main() -> int:
    arm = os.environ.get("AQ_PROOF_ARM", "").strip().lower()
    if arm not in {"baseline", "provisional", "active"}:
        raise RigFailure("AQ_PROOF_ARM must be baseline, provisional, or active; no default/skip")
    if not os.environ.get("AQ_DB_URL"):
        raise RigFailure("AQ_DB_URL is required; in-memory/SQLite/implicit DSN is forbidden")
    os.environ["AQ_CAUSAL_AUTOMINE"] = "0"
    db = Db(os.environ["AQ_DB_URL"], graph="none")
    metric = "inert_control_metric"
    try:
        db._q(f"CREATE TABLE {metric}(value integer NOT NULL)")
        db._q(f"INSERT INTO {metric} VALUES(0)")
        db._q(f"CREATE TABLE {metric}_observations(note text NOT NULL)")
        evidence_run = _seed_completed_evidence(db)
        # Two stopped works cover both causal orderings.  The first predates edge insertion and
        # catches raw provisional-edge wakeups.  The second postdates mining and proves that the
        # later PROMOTION event (not old edge.created_at) is what makes an existing edge wakeable.
        future_edge_stop = _seed_wake_fixture(db)
        edge_id = None
        if arm in {"provisional", "active"}:
            edge_id = _insert_provisional(db, evidence_run)
        existing_edge_stop = _seed_wake_fixture(db)

        provisional_wake_count = db.wake_impasse_stops()
        def wake_status(work_id):
            return db._q("SELECT status FROM work WHERE id=%s", (work_id,), one=True)["status"]
        provisional_phase = {
            "count": provisional_wake_count,
            "future_edge_stop_status": wake_status(future_edge_stop),
            "existing_edge_stop_status": wake_status(existing_edge_stop),
        }
        active_phase = None
        if arm == "active":
            # A leaking implementation may have changed state during the provisional phase. Put
            # both disposable fixtures back to the same stopped state, while preserving the red
            # receipt above, so the promoted phase remains independently observable.
            db._q("UPDATE work SET status='abandoned' WHERE id IN (%s,%s)",
                  (future_edge_stop, existing_edge_stop))
            _promote(db)
            active_wake_count = db.wake_impasse_stops()
            active_phase = {
                "count": active_wake_count,
                "future_edge_stop_status": wake_status(future_edge_stop),
                "existing_edge_stop_status": wake_status(existing_edge_stop),
            }
        wake_receipt = {"provisional_phase": provisional_phase, "active_phase": active_phase}
        # Remove only the disposable wake fixtures so a promoted edge cannot wake them again
        # inside Loop.cycle() and pre-empt the frozen DECIDE probe below.
        db._q("DELETE FROM work WHERE id IN (%s,%s)",
              (future_edge_stop, existing_edge_stop))

        inst = Instance(
            Mission(
                "prove candidate data cannot become planning authority",
                Measure("proof metric", f"SELECT value FROM {metric}", target=1),
                "one exact control", Boundaries(),
            ),
            Engine(mode="subscription"), "proof", {"graph": "none"}, Models(),
            BudgetCfg(money=Money(0, 50), autonomy=Autonomy()),
            Surfaces(notify_channel="none"), Curiosity(enabled=False),
        )
        ex = Executor(db, metric)
        result = Loop(inst, db, ex).cycle()
        receipt = _receipt(db, arm, edge_id, result, ex, wake_receipt)
        _validate(receipt)
        print(json.dumps({"ok": True, "receipt": receipt}, sort_keys=True, default=str))
        return 0
    finally:
        db.conn.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TargetControlRed as exc:
        print(json.dumps({"ok": False, "exit_class": "target-control-red", "error": str(exc),
                          "probe_sha256": PROBE_SHA256}, sort_keys=True), file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(json.dumps({"ok": False, "exit_class": "rig-failure", "error": str(exc),
                          "probe_sha256": PROBE_SHA256}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)
