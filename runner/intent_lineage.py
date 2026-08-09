"""Deterministic, de-correlated intent-lineage verifier and plan measures.

The plan author supplies lineage; this module (not the author/model) proves whether
its machine predicates entail the authoritative mission concerns.  The same DSL is
used after ACT, so intent coverage is enforceable rather than a prose/self-grade.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

VERIFIER_ID = "deterministic-interval-v1"
OPERATORS = {">", ">=", "==", "<=", "<"}


@dataclass(frozen=True)
class IntentVerification:
    valid: bool
    reasons: tuple[str, ...]
    verifier_id: str = VERIFIER_ID


@dataclass(frozen=True)
class PlanMeasures:
    goal_satisfied: bool
    steps_executed: int
    steps_confirmed: int
    intent_satisfied: bool


def _decimal(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric predicate value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"predicate value is not finite numeric: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"predicate value is not finite numeric: {value!r}")
    return result


def validate_predicate(predicate: Mapping[str, object]) -> None:
    if set(predicate) != {"metric", "operator", "value"}:
        raise ValueError("predicate requires exactly metric, operator, value")
    if not isinstance(predicate["metric"], str) or not predicate["metric"].strip():
        raise ValueError("predicate metric must be non-empty")
    if predicate["operator"] not in OPERATORS:
        raise ValueError(f"unknown predicate operator: {predicate['operator']!r}")
    _decimal(predicate["value"])


def predicate_holds(predicate: Mapping[str, object], metrics: Mapping[str, object]) -> bool:
    validate_predicate(predicate)
    metric = str(predicate["metric"])
    if metric not in metrics:
        return False
    try:
        actual, expected = _decimal(metrics[metric]), _decimal(predicate["value"])
    except ValueError:
        return False
    return {
        ">": actual > expected, ">=": actual >= expected, "==": actual == expected,
        "<=": actual <= expected, "<": actual < expected,
    }[str(predicate["operator"])]


def _bounds(predicates: Sequence[Mapping[str, object]]):
    bounds: dict[str, dict[str, tuple[Decimal, bool] | Decimal | None]] = {}
    for pred in predicates:
        validate_predicate(pred)
        metric, op, value = str(pred["metric"]), str(pred["operator"]), _decimal(pred["value"])
        b = bounds.setdefault(metric, {"lower": None, "upper": None, "equal": None})
        if op == "==":
            if b["equal"] is not None and b["equal"] != value:
                return bounds, False
            b["equal"] = value
        elif op in (">", ">="):
            candidate = (value, op == ">")
            current = b["lower"]
            if current is None or candidate[0] > current[0] or (candidate[0] == current[0] and candidate[1]):
                b["lower"] = candidate
        else:
            candidate = (value, op == "<")
            current = b["upper"]
            if current is None or candidate[0] < current[0] or (candidate[0] == current[0] and candidate[1]):
                b["upper"] = candidate
    for b in bounds.values():
        eq, lower, upper = b["equal"], b["lower"], b["upper"]
        if eq is not None:
            if lower and (eq < lower[0] or (eq == lower[0] and lower[1])): return bounds, False
            if upper and (eq > upper[0] or (eq == upper[0] and upper[1])): return bounds, False
        if lower and upper and (lower[0] > upper[0] or (lower[0] == upper[0] and (lower[1] or upper[1]))):
            return bounds, False
    return bounds, True


def predicates_entail(criteria: Sequence[Mapping[str, object]], concern: Mapping[str, object]) -> bool:
    """Whether conjunctive numeric criteria logically guarantee a concern predicate."""
    bounds, consistent = _bounds(criteria)
    if not consistent:
        return False  # reject vacuous proofs from impossible success criteria
    validate_predicate(concern)
    metric, op, value = str(concern["metric"]), str(concern["operator"]), _decimal(concern["value"])
    if metric not in bounds: return False
    b = bounds[metric]
    if b["equal"] is not None:
        return predicate_holds(concern, {metric: b["equal"]})
    if op in (">", ">="):
        lower = b["lower"]
        if lower is None: return False
        return lower[0] > value or (lower[0] == value and (op == ">=" or lower[1]))
    if op in ("<", "<="):
        upper = b["upper"]
        if upper is None: return False
        return upper[0] < value or (upper[0] == value and (op == "<=" or upper[1]))
    return False


def verify_intent_lineage(plan: Mapping[str, object], authoritative_concerns: Sequence[Mapping[str, object]]) -> IntentVerification:
    reasons: list[str] = []
    try:
        goal = plan["goal_predicate"]
        validate_predicate(goal)
        concerns = plan["mission_concerns"]
        subgoals = plan["subgoals"]
        steps = plan["steps"]
    except (KeyError, TypeError, ValueError) as exc:
        return IntentVerification(False, (f"invalid lineage structure: {exc}",))

    authored = {c.get("concern_id"): c for c in concerns if isinstance(c, dict)}
    authoritative = {c["concern_id"]: c for c in authoritative_concerns}
    if set(authored) != set(authoritative):
        reasons.append("plan concern IDs do not exactly match the authoritative mission concerns")
    for cid, expected in authoritative.items():
        actual = authored.get(cid)
        if actual is None: continue
        if actual.get("kind") != expected.get("kind") or actual.get("predicate") != expected.get("predicate"):
            reasons.append(f"concern {cid} differs from the authoritative predicate or kind")

    subgoal_by_id = {s.get("subgoal_id"): s for s in subgoals if isinstance(s, dict)}
    if len(subgoal_by_id) != len(subgoals) or None in subgoal_by_id:
        reasons.append("subgoal IDs must be present and unique")
    linked_concerns: set[str] = set()
    criteria = [goal]
    for sid, subgoal in subgoal_by_id.items():
        try:
            validate_predicate(subgoal["success_predicate"])
            criteria.append(subgoal["success_predicate"])
        except (KeyError, ValueError, TypeError) as exc:
            reasons.append(f"subgoal {sid} has invalid success predicate: {exc}")
        links = subgoal.get("serves_concern_ids") or []
        if not links or any(cid not in authoritative for cid in links):
            reasons.append(f"subgoal {sid} has missing or unknown concern linkage")
        linked_concerns.update(links)

    if linked_concerns != set(authoritative):
        reasons.append("subgoals do not cover every authoritative concern")
    step_ids: set[str] = set()
    for step in steps:
        sid, subgoal_id = step.get("step_id"), step.get("subgoal_id")
        if not sid or sid in step_ids: reasons.append("step IDs must be present and unique")
        step_ids.add(sid)
        if subgoal_id not in subgoal_by_id:
            reasons.append(f"step {sid} does not link to a known subgoal")

    for cid, concern in authoritative.items():
        if not predicates_entail(criteria, concern["predicate"]):
            reasons.append(f"satisfying plan criteria does not guarantee concern {cid}")
    return IntentVerification(not reasons, tuple(reasons))


def measure_plan(plan: Mapping[str, object], observed_metrics: Mapping[str, object],
                 step_results: Sequence[Mapping[str, object]]) -> PlanMeasures:
    goal_ok = predicate_holds(plan["goal_predicate"], observed_metrics)
    executed = sum(bool(row.get("executed")) for row in step_results)
    confirmed = sum(bool(row.get("executed")) and bool(row.get("confirmed")) for row in step_results)
    concerns = plan.get("mission_concerns") or []
    intent_ok = bool(concerns) and all(predicate_holds(c["predicate"], observed_metrics) for c in concerns)
    return PlanMeasures(goal_ok, executed, confirmed, intent_ok)


def mission_intent_contract(mission, target) -> list[dict]:
    """Build the authoritative concern set from typed mission semantics, not model prose."""
    concerns = [{
        "concern_id": "serve_mission_progress",
        "kind": "serve",
        "predicate": {"metric": "mission_delta", "operator": ">=", "value": 0},
    }]
    if mission.measure.goal == "reach_and_maintain" and target is not None:
        concerns.append({
            "concern_id": "must_not_overshoot",
            "kind": "must_not_harm",
            "predicate": {"metric": "mission_value", "operator": "<=", "value": float(target)},
        })
    return concerns
