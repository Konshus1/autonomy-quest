"""surprise_wiring.py — EXPERIMENTAL, GATED FAIL-CLOSED.

T6 (#4898): the surprise-driven refinement loop on causal edges.
Builds on T5 (#4897) planning_prediction table and uses the
ralph_portable/surprise_resolution.py packet format (ralph_surprise_packet_v0).

WHAT IT DOES
------------
On outcome, for each unresolved planning_prediction row of the plan:
  1. computes surprise = |predicted_certainty - actual_score|,
  2. fills actual_outcome + surprise_level on the prediction row,
  3. if surprise >= threshold -> emits a ralph_surprise_packet_v0 format packet,
  4. the packet is GATED fail-closed: authority = all False
     (HELD investigation intent, not an executable directive, no auto-action),
  5. if surprise confirms the edge was wrong (predicted success, actual failure):
     -> decrease support_count on the causal_edge,
     -> track falsification count (falsifying surprises for this edge),
  6. fast demotion: if falsification count >= threshold -> set falsified_by
     (marks the edge as falsified/demoted; PlanningCheck already excludes
     falsified edges from governance).

WHAT IT DOES NOT DO
-------------------
- Does NOT auto-act, auto-dispatch, or auto-mutate production.
- Does NOT change any actuator path (loop.py / executor.py untouched).
- Does NOT import or modify main-path code.
- The surprise packet is a HELD investigation intent, not an executable directive.

This module is import-safe: importing it has no side effects. It is not imported
by any main-path file (runner/loop.py, executor.py, gateway.py, ...). The test
suite asserts that.

Source of truth: BB decision #746 (causal edge model), #829 (JEPA enrichment),
surprise_resolution packet format from ralph_portable/surprise_resolution.py.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

import psycopg2
import psycopg2.extras

log = logging.getLogger("aq.experiments.causal_edges.surprise_wiring")

# ---------------------------------------------------------------------------
# Surprise packet format (mirrors ralph_portable/surprise_resolution.py)
# ---------------------------------------------------------------------------
PACKET_TYPE = "ralph_surprise_packet_v0"
PACKET_SCHEMA_VERSION = "0.1.0"
SURPRISE_TYPE = "planning_prediction_surprise"
SOURCE_SURFACE = "causal_edges_experiment"

# ---------------------------------------------------------------------------
# Thresholds (judgment-only defaults, overridable per instance)
# ---------------------------------------------------------------------------
SURPRISE_THRESHOLD = 0.3       # surprise_level >= this -> surprise packet emitted
FALSIFICATION_THRESHOLD = 3     # enough falsifying surprises -> demote edge

# Outcome -> numeric score in [0, 1] (1 = success, 0 = failure)
_OUTCOME_SCORES: dict[str, float] = {
    "success": 1.0,
    "failure": 0.0,
    "partial": 0.5,
}


def _outcome_score(actual_outcome: str) -> float:
    """Map an outcome label to a numeric score in [0, 1]."""
    return _OUTCOME_SCORES.get(actual_outcome.lower(), 0.0)


def _severity_for_surprise(surprise_level: float) -> str:
    """Map a surprise level to a severity literal recognized by surprise_resolution."""
    if surprise_level >= 0.7:
        return "critical"
    if surprise_level >= 0.5:
        return "high"
    if surprise_level >= 0.3:
        return "medium"
    return "low"


@dataclass
class SurprisePacket:
    """A surprise packet in ralph_surprise_packet_v0 wire format.

    Carries the same fields validate_surprise_packet_v0 expects, plus internal
    bookkeeping (edge_id, prediction_id, surprise_level) used by the weight
    update + demotion pipeline. The wire-format dict is produced by to_dict().
    """

    task_id: int
    parent_task_id: int
    surprise_type: str
    source_surface: str
    expected_outcome: dict[str, Any]
    actual_outcome: dict[str, Any]
    impact_on_goals: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    severity: str
    likely_causal_hypotheses: list[dict[str, Any]]
    recommended_follow_up: dict[str, Any]
    likely_missing_variable: Any
    proposed_model_update: dict[str, Any]
    outer_loop_learning_fields: dict[str, Any]
    spawn_recommended: bool
    spawn_gate_reason: str

    # internal bookkeeping (not part of the wire format)
    edge_id: int = 0
    prediction_id: int = 0
    surprise_level: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the ralph_surprise_packet_v0 wire format."""
        return {
            "packet_type": PACKET_TYPE,
            "schema_version": PACKET_SCHEMA_VERSION,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "source_surface": self.source_surface,
            "surprise_type": self.surprise_type,
            "expected_outcome": self.expected_outcome,
            "actual_outcome": self.actual_outcome,
            "impact_on_goals": self.impact_on_goals,
            "evidence_refs": self.evidence_refs,
            "severity": self.severity,
            "likely_causal_hypotheses": self.likely_causal_hypotheses,
            "recommended_follow_up": self.recommended_follow_up,
            "likely_missing_variable": self.likely_missing_variable,
            "proposed_model_update": self.proposed_model_update,
            "outer_loop_learning_fields": self.outer_loop_learning_fields,
            "spawn_recommended": self.spawn_recommended,
            "spawn_gate_reason": self.spawn_gate_reason,
        }

    @property
    def authority(self) -> dict[str, bool]:
        """Gated fail-closed: all authority flags False. HELD investigation.

        Mirrors the authority mapping from
        ralph_portable/surprise_resolution.py SurpriseResolutionIntent.
        """
        return {
            "may_dispatch_workers": False,
            "may_call_providers": False,
            "may_mutate_scheduler": False,
            "may_mutate_production": False,
            "may_accept_model_update": False,
            "requires_manager_review": True,
        }


class SurpriseWiring:
    """Surprise-driven refinement loop on causal edges. GATED FAIL-CLOSED.

    All methods are additive: they read/write only the experimental tables
    (causal_edge, planning_prediction) created by 009_causal_edges.sql.
    No main-path table is touched. No actuator path is changed.
    """

    def __init__(
        self,
        conn: psycopg2.extensions.connection,
        *,
        surprise_threshold: float = SURPRISE_THRESHOLD,
        falsification_threshold: int = FALSIFICATION_THRESHOLD,
    ) -> None:
        self.conn = conn
        self.surprise_threshold = surprise_threshold
        self.falsification_threshold = falsification_threshold

    # ------------------------------------------------------------------
    # 1. On outcome: compute actual vs predicted, fill prediction rows
    # ------------------------------------------------------------------
    def resolve_outcome(
        self,
        plan_id: str,
        actual_outcome: str,
        *,
        task_id: int = 1,
        evidence_run_id: int | None = None,
    ) -> list[SurprisePacket]:
        """Fill actual_outcome + surprise_level on each unresolved prediction.

        Returns the list of SurprisePacket objects emitted (empty if no
        prediction crossed the surprise threshold).
        """
        actual_score = _outcome_score(actual_outcome)

        # Read unresolved predictions for this plan
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT prediction_id, edge_id, plan_id, predicted_certainty,
                       predicted_principles_supported,
                       predicted_principles_challenged,
                       predicted_edges_stressed
                  FROM planning_prediction
                 WHERE plan_id = %s AND actual_outcome IS NULL
                """,
                (plan_id,),
            )
            predictions = [dict(r) for r in cur.fetchall()]

        packets: list[SurprisePacket] = []
        for pred in predictions:
            predicted = pred["predicted_certainty"] or 0.5
            surprise = abs(predicted - actual_score)
            surprise = max(0.0, min(1.0, surprise))

            # Fill actual_outcome + surprise_level on the prediction row
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE planning_prediction
                       SET actual_outcome = %s, surprise_level = %s
                     WHERE prediction_id = %s
                    """,
                    (actual_outcome, round(surprise, 4), pred["prediction_id"]),
                )
            self.conn.commit()

            if surprise >= self.surprise_threshold:
                packet = self._build_packet(
                    pred, actual_outcome, surprise, task_id, evidence_run_id
                )
                packets.append(packet)

                # Weight update: if surprise confirms the edge was wrong
                # (predicted success but actual failure)
                if actual_outcome.lower() == "failure" and predicted >= 0.5:
                    self._weight_update(pred["edge_id"], evidence_run_id)

        log.info(
            "resolved %d prediction(s) for plan '%s', %d surprise packet(s) emitted",
            len(predictions),
            plan_id,
            len(packets),
        )
        return packets

    # ------------------------------------------------------------------
    # 2. Build surprise packet (ralph_surprise_packet_v0 format)
    # ------------------------------------------------------------------
    def _build_packet(
        self,
        prediction: dict[str, Any],
        actual_outcome: str,
        surprise_level: float,
        task_id: int,
        evidence_run_id: int | None,
    ) -> SurprisePacket:
        """Build a surprise packet in ralph_surprise_packet_v0 wire format."""
        severity = _severity_for_surprise(surprise_level)
        predicted = prediction["predicted_certainty"] or 0.5
        edge_id = prediction["edge_id"]
        pred_id = prediction["prediction_id"]

        return SurprisePacket(
            task_id=task_id,
            parent_task_id=task_id,
            surprise_type=SURPRISE_TYPE,
            source_surface=SOURCE_SURFACE,
            expected_outcome={
                "certainty": round(predicted, 4),
                "predicted_outcome": "success" if predicted >= 0.5 else "failure",
            },
            actual_outcome={
                "outcome": actual_outcome,
                "score": _outcome_score(actual_outcome),
            },
            impact_on_goals={
                "edge_id": edge_id,
                "surprise_level": round(surprise_level, 4),
            },
            evidence_refs=[
                {
                    "locator": f"planning_prediction:{pred_id}",
                    "edge_id": edge_id,
                }
            ],
            severity=severity,
            likely_causal_hypotheses=[
                {
                    "hypothesis": (
                        f"Edge {edge_id} prediction was wrong: "
                        f"predicted certainty {predicted:.2f} but actual "
                        f"outcome was '{actual_outcome}'."
                    ),
                    "confidence": "low",
                }
            ],
            recommended_follow_up={
                "action": "investigate",
                "gate": "held_fail_closed",
            },
            likely_missing_variable=None,
            proposed_model_update={
                "decrease_support": True,
                "falsify": False,
                "edge_id": edge_id,
            },
            outer_loop_learning_fields={
                "surprise_level": round(surprise_level, 4),
                "edge_id": edge_id,
                "plan_id": prediction["plan_id"],
            },
            spawn_recommended=False,
            spawn_gate_reason=(
                "fail-closed: no auto-action under experimental constraint"
            ),
            edge_id=edge_id,
            prediction_id=pred_id,
            surprise_level=round(surprise_level, 4),
        )

    # ------------------------------------------------------------------
    # 3. Weight update: decrease support_count, track falsifications
    # ------------------------------------------------------------------
    def _weight_update(self, edge_id: int, evidence_run_id: int | None) -> None:
        """Decrease support_count; track falsification; demote if threshold reached.

        Called when a surprise confirms the edge was wrong (predicted success,
        actual failure). Decreases support_count by 1 (not below 0), then
        checks if the accumulated falsification count has reached the
        demotion threshold.
        """
        with self.conn.cursor() as cur:
            # Decrease support_count (not below 0)
            cur.execute(
                """
                UPDATE causal_edge
                   SET support_count = GREATEST(support_count - 1, 0),
                       updated_at = now()
                 WHERE edge_id = %s AND falsified_by IS NULL
                """,
                (edge_id,),
            )
            self.conn.commit()

        log.info(
            "weight update: decreased support_count for edge %s", edge_id
        )

        # Check falsification count: count planning_prediction rows for this
        # edge where actual_outcome = 'failure' and surprise_level >= threshold
        falsification_count = self._count_falsifications(edge_id)

        if falsification_count >= self.falsification_threshold:
            self._demote(edge_id, evidence_run_id)

    def _count_falsifications(self, edge_id: int) -> int:
        """Count falsifying surprises for an edge.

        A falsifying surprise is a planning_prediction row for this edge where
        actual_outcome = 'failure' and surprise_level >= surprise_threshold.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM planning_prediction
                 WHERE edge_id = %s
                   AND actual_outcome = 'failure'
                   AND surprise_level >= %s
                """,
                (edge_id, self.surprise_threshold),
            )
            return cur.fetchone()[0]

    # ------------------------------------------------------------------
    # 4. Fast demotion: mark edge as falsified
    # ------------------------------------------------------------------
    def _demote(self, edge_id: int, evidence_run_id: int | None) -> None:
        """Fast demotion: set falsified_by on the causal_edge.

        Setting falsified_by marks the edge as falsified/demoted. PlanningCheck
        already excludes falsified edges (WHERE falsified_by IS NULL), so a
        demoted edge no longer governs any plan.
        """
        run_id = evidence_run_id or 1  # fallback if no run id provided
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE causal_edge
                   SET falsified_by = %s, updated_at = now()
                 WHERE edge_id = %s AND falsified_by IS NULL
                """,
                (run_id, edge_id),
            )
            self.conn.commit()
        log.info("demoted edge %s (falsified_by = %s)", edge_id, run_id)

    # ------------------------------------------------------------------
    # 5. Full pipeline: resolve -> surprise -> gate -> weight update
    # ------------------------------------------------------------------
    def process_outcome(
        self,
        plan_id: str,
        actual_outcome: str,
        *,
        task_id: int = 1,
        evidence_run_id: int | None = None,
    ) -> tuple[list[SurprisePacket], list[int]]:
        """Full surprise wiring pipeline.

        Returns (packets, demoted_edge_ids).
        GATED FAIL-CLOSED: no auto-action. Packets are HELD investigation
        intents with all authority flags False.
        """
        packets = self.resolve_outcome(
            plan_id,
            actual_outcome,
            task_id=task_id,
            evidence_run_id=evidence_run_id,
        )

        # Check which surprised edges ended up demoted
        demoted: list[int] = []
        seen_edges: set[int] = set()
        for p in packets:
            if p.edge_id and p.edge_id not in seen_edges:
                seen_edges.add(p.edge_id)
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT falsified_by FROM causal_edge WHERE edge_id = %s",
                        (p.edge_id,),
                    )
                    row = cur.fetchone()
                    if row is not None and row[0] is not None:
                        demoted.append(p.edge_id)

        return packets, demoted
