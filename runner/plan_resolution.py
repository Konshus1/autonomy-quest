"""Localize plan refutations without discarding a confirmed prefix."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class StepResolution:
    step_id: str
    executed: bool
    direct_effect_confirmed: bool
    direction_confirmed: bool
    outcome_kind: str
    evidence: str


@dataclass(frozen=True)
class PlanResolution:
    steps: tuple[StepResolution, ...]
    failed_step_id: str | None
    preserved_prefix_step_ids: tuple[str, ...]
    failure_reason: str | None


def resolve_plan_steps(plan_steps: Sequence[Mapping[str, object]],
                       step_results: Sequence[Mapping[str, object]], *,
                       goal_satisfied: bool, intent_satisfied: bool) -> PlanResolution:
    by_id = {str(r.get("step_id")): r for r in step_results}
    resolutions: list[StepResolution] = []
    for step in plan_steps:
        sid = str(step["step_id"]); result = by_id.get(sid, {})
        executed = bool(result.get("executed")); direct = executed and bool(result.get("confirmed"))
        harms = tuple(result.get("harmed_concern_ids") or ())
        if not executed: kind, direction = "not_executed", False
        elif not direct: kind, direction = "direct_effect_refuted", False
        elif harms: kind, direction = "intent_harm", False
        else: kind, direction = "supported", True
        resolutions.append(StepResolution(sid, executed, direct, direction, kind,
                                          str(result.get("evidence") or "")))

    failed_index = next((i for i, row in enumerate(resolutions)
                         if row.executed and not row.direction_confirmed), None)
    reason = resolutions[failed_index].outcome_kind if failed_index is not None else None
    if failed_index is None and not intent_satisfied:
        # If an ACT omitted per-step harm attribution, fail closed at the last executed step.
        failed_index = next((i for i in range(len(resolutions)-1, -1, -1)
                             if resolutions[i].executed), None)
        reason = "intent_not_satisfied"
    if failed_index is None and not goal_satisfied:
        # Correct direct effects can still be irrelevant to the plan goal.
        failed_index = next((i for i in range(len(resolutions)-1, -1, -1)
                             if resolutions[i].executed), None)
        reason = "goal_not_satisfied"
    if failed_index is None:
        return PlanResolution(tuple(resolutions), None,
                              tuple(r.step_id for r in resolutions if r.direction_confirmed), None)
    # Prefix means consecutive direction-confirmed steps strictly before the refutation.
    prefix = []
    for row in resolutions[:failed_index]:
        if not row.direction_confirmed: break
        prefix.append(row.step_id)
    failed = resolutions[failed_index].step_id
    # Re-label a supported direct effect when the composed direction/intent failed.
    if resolutions[failed_index].outcome_kind == "supported":
        row = resolutions[failed_index]
        resolutions[failed_index] = StepResolution(
            row.step_id, row.executed, row.direct_effect_confirmed, False,
            reason or "composed_direction_refuted", row.evidence)
    return PlanResolution(tuple(resolutions), failed, tuple(prefix), reason)
