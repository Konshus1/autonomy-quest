"""planning_check.py — EXPERIMENTAL, RECORD-ONLY.

T5 (#4897): a judgment-only planning check that reads governing causal edges and
records a PREDICTED CERTAINTY per plan. This is the CAUSAL WORLD MODEL substrate
(BB #746 first slice) enriched by the JEPA outcome representation (BB #829 REC 2).

WHAT IT DOES
------------
Given a plan (an opaque identifier + a set of actions it touches), it:
  1. finds the governing causal_edges whose `source_action` the plan exercises,
  2. predicts, per touched edge:
       - which principles (edges) will be SUPPORTED by executing this plan,
       - which principles (edges) will be CHALLENGED (stressed / falsified),
       - which edges will be STRESSED (load-bearing for this plan),
       - a predicted_certainty (composite of the edge's three weights),
  3. writes ONE planning_prediction row per touched edge.

WHAT IT DOES NOT DO
-------------------
- It does NOT change any actuator path. loop.py / executor.py are untouched.
- It does NOT gate, block, redirect, or veto a plan.
- It does NOT promote, demote, or falsify edges.
- It does NOT compute actual_outcome or surprise_level — those are filled by a
  LATER slice on outcome. Here we only RECORD the prediction.

This module is import-safe: importing it has no side effects. It is not imported
by any main-path file (runner/loop.py, executor.py, gateway.py, ...). The test
suite asserts that.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import psycopg2
import psycopg2.extras

log = logging.getLogger("aq.experiments.causal_edges.planning_check")

# Weight defaults mirror the migration defaults (009_causal_edges.sql).
_DEFAULT_FORMALITY = 0.5
_DEFAULT_STRICTNESS = 0.5
_DEFAULT_DIRECTNESS = 0.5


@dataclass
class Plan:
    """A plan the check reasons about. `plan_id` is opaque (not a DB FK)."""

    plan_id: str
    # actions this plan exercises — matched against causal_edge.source_action
    actions: list[str] = field(default_factory=list)
    # free-text rationale; recorded for auditability, not used for gating
    rationale: str = ""


@dataclass
class EdgePrediction:
    """The prediction for one (plan, edge) pair — full outcome representation."""

    edge_id: int
    plan_id: str
    predicted_principles_supported: list[str]
    predicted_principles_challenged: list[str]
    predicted_edges_stressed: list[int]
    predicted_certainty: float


def _composite_certainty(formality: float, strictness: float, directness: float) -> float:
    """Combine the three independent weights into a single predicted certainty.

    This is a JUDGMENT-ONLY heuristic, not a learned model. The formality weight
    (epistemic confidence) dominates — a low-confidence edge yields low predicted
    certainty regardless of how strictly it binds or how direct its action is.
    Strictness and directness modulate upward: a strict, direct, well-supported
    edge is the most predictable. All three are in [0,1]; result clamped to [0,1].
    """
    raw = formality * (0.5 + 0.25 * strictness + 0.25 * directness)
    return max(0.0, min(1.0, raw))


class PlanningCheck:
    """RECORD-ONLY. Reads governing edges; records predicted certainty per plan."""

    def __init__(self, conn: psycopg2.extensions.connection) -> None:
        self.conn = conn

    # -- read: which governing edges does this plan touch? -------------------
    def governing_edges(self, plan: Plan) -> list[dict[str, Any]]:
        """Return the non-falsified causal_edges whose source_action the plan exercises.

        An edge is 'governing' for a plan when the plan's actions intersect the
        edge's source_action. Falsified edges (falsified_by IS NOT NULL) are
        excluded — they no longer govern.
        """
        if not plan.actions:
            return []
        sql = """
            SELECT edge_id, source_action, direct_effect, mission_measure,
                   formality_weight, strictness_weight, directness_weight,
                   executor_slot, predicted_certainty, scope_conditions
              FROM causal_edge
             WHERE falsified_by IS NULL
               AND source_action = ANY(%s)
             ORDER BY strictness_weight DESC, formality_weight DESC
        """
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (plan.actions,))
            return [dict(r) for r in cur.fetchall()]

    # -- predict: full outcome representation per touched edge ----------------
    def predict(self, plan: Plan, edges: Sequence[dict[str, Any]]) -> list[EdgePrediction]:
        """For each governing edge, build the full outcome prediction (BB #829 REC 2).

        - predicted_principles_supported: edges whose source_action the plan
          exercises AND that have formality_weight >= 0.5 (well-supported) —
          executing the plan should reinforce them.
        - predicted_principles_challenged: edges whose source_action the plan
          exercises but with formality_weight < 0.5 (weakly supported) — the
          plan is a test of whether they hold.
        - predicted_edges_stressed: the edge_ids of all governing edges (every
          governing edge is load-bearing for a plan that exercises it; per BB
          #829 / DR7 counterfactual discipline, if removing the edge would
          change the prediction, the edge is stressed).
        - predicted_certainty: composite of the three weights.
        """
        supported: list[str] = []
        challenged: list[str] = []
        stressed_ids: list[int] = []
        for e in edges:
            stressed_ids.append(e["edge_id"])
            if e["formality_weight"] >= 0.5:
                supported.append(e["source_action"])
            else:
                challenged.append(e["source_action"])

        preds: list[EdgePrediction] = []
        for e in edges:
            certainty = _composite_certainty(
                e.get("formality_weight") or _DEFAULT_FORMALITY,
                e.get("strictness_weight") or _DEFAULT_STRICTNESS,
                e.get("directness_weight") or _DEFAULT_DIRECTNESS,
            )
            preds.append(EdgePrediction(
                edge_id=e["edge_id"],
                plan_id=plan.plan_id,
                predicted_principles_supported=list(supported),
                predicted_principles_challenged=list(challenged),
                predicted_edges_stressed=list(stressed_ids),
                predicted_certainty=round(certainty, 4),
            ))
        return preds

    # -- record: write ONE planning_prediction row per touched edge -----------
    def record(self, predictions: Iterable[EdgePrediction]) -> int:
        """RECORD-ONLY. Insert planning_prediction rows. Returns count inserted.

        Does NOT update causal_edge. Does NOT touch any main-path table.
        actual_outcome / surprise_level are left NULL — filled on outcome later.
        """
        rows = list(predictions)
        if not rows:
            return 0
        sql = """
            INSERT INTO planning_prediction
                (edge_id, plan_id,
                 predicted_principles_supported,
                 predicted_principles_challenged,
                 predicted_edges_stressed,
                 predicted_certainty)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        params = [
            (p.edge_id, p.plan_id,
             json.dumps(p.predicted_principles_supported),
             json.dumps(p.predicted_principles_challenged),
             json.dumps(p.predicted_edges_stressed),
             p.predicted_certainty)
            for p in rows
        ]
        with self.conn.cursor() as cur:
            cur.executemany(sql, params)
        self.conn.commit()
        log.info("recorded %d planning_prediction row(s)", len(rows))
        return len(rows)

    # -- the full record-only pipeline ---------------------------------------
    def check_and_record(self, plan: Plan) -> list[EdgePrediction]:
        """Run the record-only planning check end to end.

        governing_edges -> predict -> record. Returns the predictions written.
        No actuator path is changed. No plan is gated or redirected.
        """
        edges = self.governing_edges(plan)
        preds = self.predict(plan, edges)
        self.record(preds)
        return preds
