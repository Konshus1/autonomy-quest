"""Goal-relative, direction-first plan sufficiency.

This module implements BB #2430 / task #5001's three-state criterion.  It is
pure and deterministic so DECIDE can call it before any action and tests can
exercise the semantic boundary without a model or network call.

The crucial invariant is that a known direction is enough: an unknown mechanism
makes an edge *fuzzy*, not absent.  Only an unknown direction is a frame gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, Sequence


class Direction(StrEnum):
    TOWARD = "toward"
    AWAY = "away"
    NEUTRAL = "neutral"


class EdgeState(StrEnum):
    SOLID = "solid"
    FUZZY = "fuzzy"
    ABSENT = "absent"


class ConflictSeverity(StrEnum):
    SOFT = "soft"
    HARD = "hard"


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    action: str
    expected_effect: str
    expected_direction: Direction
    scope: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class KnownRelation:
    relation_id: str
    action: str
    effect: str
    direction: Direction
    mechanism: str | None = None
    scope: Mapping[str, object] = field(default_factory=dict)
    certainty: float = 0.5
    principle_id: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.certainty <= 1.0:
            raise ValueError("certainty must be between 0 and 1")


@dataclass(frozen=True)
class RelationConflict:
    left_relation_id: str
    right_relation_id: str
    severity: ConflictSeverity
    reason: str
    step_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepAssessment:
    step_id: str
    edge_state: EdgeState
    sufficient: bool
    expected_direction: Direction
    relation_id: str | None
    principle_id: str | None
    certainty: float
    annotation: str | None = None


@dataclass(frozen=True)
class SufficiencyResult:
    sufficient: bool
    blocked: bool
    steps: tuple[StepAssessment, ...]
    frame_gap_step_ids: tuple[str, ...]
    hard_conflicts: tuple[RelationConflict, ...]
    soft_tensions: tuple[RelationConflict, ...]

    @property
    def replan_from_step_id(self) -> str | None:
        """First localized point that cannot yet support the plan."""
        if self.frame_gap_step_ids:
            return self.frame_gap_step_ids[0]
        if self.hard_conflicts:
            ids = self.hard_conflicts[0].step_ids
            return ids[0] if ids else None
        return None


def _scope_matches(step_scope: Mapping[str, object], relation_scope: Mapping[str, object]) -> bool:
    """A relation governs only when all of its declared scope matches the step."""
    return all(step_scope.get(key) == value for key, value in relation_scope.items())


def assess_plan_sufficiency(
    steps: Sequence[PlanStep],
    relations: Sequence[KnownRelation],
    conflicts: Sequence[RelationConflict] = (),
    *,
    soft_tension_penalty: float = 0.2,
) -> SufficiencyResult:
    """Assess a proposed path without demanding complete mechanisms.

    * solid: exact scoped relation, direction agrees, mechanism known
    * fuzzy: exact scoped relation, direction agrees, mechanism unknown
    * absent: no exact scoped relation supplies a direction

    A relation that predicts the opposite direction is an explicit hard
    contradiction, localized to the step.  Declared soft tensions annotate and
    degrade certainty but never turn a directional edge into a frame gap.
    """
    if not 0.0 <= soft_tension_penalty <= 1.0:
        raise ValueError("soft_tension_penalty must be between 0 and 1")

    assessments: list[StepAssessment] = []
    derived_hard: list[RelationConflict] = []
    soft = [c for c in conflicts if c.severity == ConflictSeverity.SOFT]
    hard = [c for c in conflicts if c.severity == ConflictSeverity.HARD]

    by_id = {r.relation_id: r for r in relations}
    for step in steps:
        candidates = [
            r for r in relations
            if r.action == step.action
            and r.effect == step.expected_effect
            and _scope_matches(step.scope, r.scope)
        ]
        agreeing = [r for r in candidates if r.direction == step.expected_direction]
        if not agreeing:
            disagreeing = [r for r in candidates if r.direction != step.expected_direction]
            if disagreeing:
                relation = max(disagreeing, key=lambda r: r.certainty)
                conflict = RelationConflict(
                    relation.relation_id,
                    f"plan-step:{step.step_id}",
                    ConflictSeverity.HARD,
                    f"relation predicts {relation.direction.value}, plan asserts {step.expected_direction.value}",
                    (step.step_id,),
                )
                derived_hard.append(conflict)
                assessments.append(StepAssessment(
                    step.step_id, EdgeState.ABSENT, False, step.expected_direction,
                    relation.relation_id, relation.principle_id, 0.0,
                    conflict.reason,
                ))
            else:
                assessments.append(StepAssessment(
                    step.step_id, EdgeState.ABSENT, False, step.expected_direction,
                    None, None, 0.0,
                    "no known in-scope relation supplies a direction",
                ))
            continue

        relation = max(agreeing, key=lambda r: r.certainty)
        state = EdgeState.SOLID if relation.mechanism and relation.mechanism.strip() else EdgeState.FUZZY
        step_tensions = [
            c for c in soft
            if not c.step_ids or step.step_id in c.step_ids
            if c.left_relation_id == relation.relation_id or c.right_relation_id == relation.relation_id
        ]
        certainty = max(0.0, relation.certainty - soft_tension_penalty * len(step_tensions))
        annotation = "; ".join(c.reason for c in step_tensions) or None
        assessments.append(StepAssessment(
            step.step_id, state, True, step.expected_direction,
            relation.relation_id, relation.principle_id, round(certainty, 6), annotation,
        ))

    all_hard = tuple(hard + derived_hard)
    hard_step_ids = {step_id for c in all_hard for step_id in c.step_ids}
    # A declared hard conflict without explicit step ids still blocks globally.
    blocked = bool(all_hard)
    frame_gaps = tuple(
        a.step_id for a in assessments
        if a.edge_state == EdgeState.ABSENT and a.step_id not in hard_step_ids
    )
    return SufficiencyResult(
        sufficient=not blocked and not frame_gaps and all(a.sufficient for a in assessments),
        blocked=blocked,
        steps=tuple(assessments),
        frame_gap_step_ids=frame_gaps,
        hard_conflicts=all_hard,
        soft_tensions=tuple(soft),
    )
