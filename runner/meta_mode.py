"""Risk-constrained net-value arbitration for planning impasses.

This is an operational, myopic approximation to rational metareasoning, not a new
VOI formalism.  Every commensurable quantity uses coarse mission-utility points:
one point is one operator-declared band of progress toward the current goal.  Hard
safety/authority constraints remain constraints and are never traded for points.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Iterable

POLICY_VERSION = "impasse-net-value-interval-v1"
MAX_UTILITY_BAND = Decimal("4")


class MetaMode(StrEnum):
    INTERNAL_COMPUTATION = "internal_computation"
    ENVIRONMENT_EXPERIMENT = "environment_experiment"
    HUMAN_QUESTION = "human_question"
    HUMAN_DEMONSTRATION = "human_demonstration"
    AUTONOMOUS_PRACTICE = "autonomous_practice"
    GOAL_RELAXATION = "goal_relaxation"
    ABSTAIN = "abstain"


class EstimateState(StrEnum):
    BOUNDED = "bounded"
    UNKNOWN = "unknown"
    KNOWN_WORTHLESS = "known_worthless"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Interval:
    low: Decimal
    high: Decimal

    @classmethod
    def parse(cls, value: Any, *, field: str) -> "Interval":
        if not isinstance(value, dict) or set(value) != {"low", "high"}:
            raise ValueError(f"{field} must contain exactly low and high")
        try:
            low, high = Decimal(str(value["low"])), Decimal(str(value["high"]))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field} bounds must be finite decimals") from exc
        if not low.is_finite() or not high.is_finite() or low < 0 or high < low:
            raise ValueError(f"{field} bounds must be finite, non-negative, and ordered")
        if high > MAX_UTILITY_BAND:
            raise ValueError(f"{field} exceeds the {MAX_UTILITY_BAND}-point coarse rubric")
        return cls(low, high)

    def json(self) -> dict[str, str]:
        return {"low": str(self.low), "high": str(self.high)}


@dataclass(frozen=True)
class ModeEstimate:
    mode: MetaMode
    state: EstimateState
    direct_value: Interval | None
    information_value: Interval | None
    cost: Interval | None
    evidence_refs: tuple[str, ...]
    rationale: str
    instruction: str
    expected_expense_usd: Decimal
    blast_radius_level: int
    reversible: bool
    spends_money: bool
    touches_human: bool
    commits: bool
    block_reason: str | None = None
    wake_condition: str | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "ModeEstimate":
        if not isinstance(raw, dict):
            raise ValueError("meta-mode estimate must be an object")
        mode, state = MetaMode(raw["mode"]), EstimateState(raw["state"])
        evidence = tuple(str(x).strip() for x in (raw.get("evidence_refs") or ()) if str(x).strip())
        rationale = str(raw.get("rationale") or "").strip()
        instruction = str(raw.get("instruction") or "").strip()
        block_reason = str(raw.get("block_reason") or "").strip() or None
        wake = str(raw.get("wake_condition") or "").strip() or None
        try:
            expense = Decimal(str(raw.get("expected_expense_usd", 0)))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{mode.value} expected_expense_usd must be finite") from exc
        blast = raw.get("blast_radius_level", 0)
        flags = {name: raw.get(name, False) for name in
                 ("reversible", "spends_money", "touches_human", "commits")}
        if (not expense.is_finite() or expense < 0 or isinstance(blast, bool)
                or not isinstance(blast, int) or not 0 <= blast <= 3
                or any(not isinstance(value, bool) for value in flags.values())):
            raise ValueError(f"{mode.value} has invalid consequence metadata")
        if (state == EstimateState.BOUNDED
                and mode in {MetaMode.HUMAN_QUESTION, MetaMode.HUMAN_DEMONSTRATION}
                and not flags["touches_human"]):
            raise ValueError(f"{mode.value} must declare touches_human")
        if not rationale:
            raise ValueError(f"{mode.value} requires a rationale")
        if state in {EstimateState.BOUNDED, EstimateState.KNOWN_WORTHLESS}:
            if not evidence:
                raise ValueError(f"{mode.value} bounded estimates require evidence provenance")
            direct = Interval.parse(raw.get("direct_value"), field=f"{mode.value}.direct_value")
            info = Interval.parse(raw.get("information_value"), field=f"{mode.value}.information_value")
            cost = Interval.parse(raw.get("cost"), field=f"{mode.value}.cost")
            if not instruction and mode not in {MetaMode.ABSTAIN, MetaMode.GOAL_RELAXATION}:
                raise ValueError(f"{mode.value} requires an executable instruction")
        else:
            if any(raw.get(name) is not None for name in ("direct_value", "information_value", "cost")):
                raise ValueError(f"{mode.value} {state.value} must not masquerade as numeric zero")
            direct = info = cost = None
        if state == EstimateState.BLOCKED and not block_reason:
            raise ValueError(f"{mode.value} blocked estimate requires block_reason")
        if state == EstimateState.UNKNOWN and not wake:
            raise ValueError(f"{mode.value} unknown estimate requires a wake_condition")
        estimate = cls(mode, state, direct, info, cost, evidence, rationale, instruction,
                       expense, blast, flags["reversible"], flags["spends_money"],
                       flags["touches_human"], flags["commits"], block_reason, wake)
        if state == EstimateState.KNOWN_WORTHLESS and estimate.net_interval().high > 0:
            raise ValueError(f"{mode.value} known_worthless requires a non-positive upper net bound")
        if mode == MetaMode.ABSTAIN and state in {EstimateState.BOUNDED, EstimateState.KNOWN_WORTHLESS}:
            if any(bound != Decimal("0") for interval in (direct, info, cost)
                   for bound in (interval.low, interval.high)):
                raise ValueError("abstain is the zero incremental-utility baseline")
            if not wake:
                raise ValueError("abstain requires a concrete wake_condition")
        return estimate

    def net_interval(self) -> Interval:
        if self.direct_value is None or self.information_value is None or self.cost is None:
            raise ValueError(f"{self.mode.value} has no commensurable score")
        return Interval(self.direct_value.low + self.information_value.low - self.cost.high,
                        self.direct_value.high + self.information_value.high - self.cost.low)


@dataclass(frozen=True)
class MetaModeDecision:
    policy_version: str
    decision: str
    chosen_mode: MetaMode | None
    chosen_score: Decimal | None
    stop_reason: str | None
    scorecards: tuple[dict[str, Any], ...]
    chosen_instruction: str | None

    @property
    def stopped(self) -> bool:
        return self.decision != "acquire"

    def json(self) -> dict[str, Any]:
        return {"policy_version": self.policy_version, "decision": self.decision,
                "chosen_mode": self.chosen_mode.value if self.chosen_mode else None,
                "chosen_score": str(self.chosen_score) if self.chosen_score is not None else None,
                "stop_reason": self.stop_reason, "scorecards": list(self.scorecards),
                "chosen_instruction": self.chosen_instruction}


def choose_meta_mode(raw_estimates: Iterable[dict[str, Any] | ModeEstimate]) -> MetaModeDecision:
    """Choose the highest conservative net value, or explicitly stop.

    The common score is the lower bound of direct mission value + decision-relevant
    information value - incremental VOC.  This pessimistic score prevents midpoint
    optimism from laundering broad uncertainty into value.  UNKNOWN is a typed state,
    never a zero score; unresolved feasible unknowns stop selection for estimation.
    """
    estimates = tuple(e if isinstance(e, ModeEstimate) else ModeEstimate.parse(e)
                      for e in raw_estimates)
    if not estimates:
        raise ValueError("at least one meta-mode estimate is required")
    modes = [e.mode for e in estimates]
    if len(set(modes)) != len(modes):
        raise ValueError("meta-mode estimates must have unique modes")
    if MetaMode.ABSTAIN not in modes:
        raise ValueError("abstain must be scored explicitly")
    missing = sorted(mode.value for mode in MetaMode if mode not in modes)
    if missing:
        raise ValueError(f"every meta-mode must be scored or explicitly blocked; missing {missing}")

    scorecards: list[dict[str, Any]] = []
    unresolved = []
    candidates: list[tuple[Decimal, Decimal, str, ModeEstimate]] = []
    for estimate in sorted(estimates, key=lambda e: e.mode.value):
        card: dict[str, Any] = {"mode": estimate.mode.value, "state": estimate.state.value,
                                "rationale": estimate.rationale,
                                "evidence_refs": list(estimate.evidence_refs),
                                "block_reason": estimate.block_reason,
                                "wake_condition": estimate.wake_condition,
                                "expected_expense_usd": str(estimate.expected_expense_usd),
                                "blast_radius_level": estimate.blast_radius_level,
                                "reversible": estimate.reversible,
                                "spends_money": estimate.spends_money,
                                "touches_human": estimate.touches_human,
                                "commits": estimate.commits}
        if estimate.state == EstimateState.UNKNOWN:
            unresolved.append(estimate)
            card.update(net_value=None, net_interval=None)
        elif estimate.state == EstimateState.BLOCKED:
            card.update(net_value=None, net_interval=None)
        else:
            net = estimate.net_interval()
            card.update(net_value=str(net.low), net_interval=net.json(),
                        direct_value=estimate.direct_value.json(),
                        information_value=estimate.information_value.json(),
                        cost=estimate.cost.json())
            candidates.append((net.low, net.high, estimate.mode.value, estimate))
        scorecards.append(card)

    if unresolved:
        return MetaModeDecision(POLICY_VERSION, "stop", None, None, "unresolved_unknown",
                                tuple(scorecards), None)
    if not candidates:
        return MetaModeDecision(POLICY_VERSION, "stop", None, None, "no_feasible_option",
                                tuple(scorecards), None)
    # Highest conservative score, then highest upper bound, then stable canonical mode.
    best = sorted(candidates, key=lambda x: (-x[0], -x[1], x[2]))[0]
    score, _, _, estimate = best
    if estimate.mode == MetaMode.ABSTAIN:
        reason = "no_option_worth_cost" if all(
            c[3].mode == MetaMode.ABSTAIN or c[1] <= 0 for c in candidates
        ) else "abstention_highest_net_value"
        return MetaModeDecision(POLICY_VERSION, "abstain", estimate.mode, score, reason,
                                tuple(scorecards), None)
    if estimate.mode == MetaMode.GOAL_RELAXATION:
        return MetaModeDecision(POLICY_VERSION, "stop", estimate.mode, score,
                                "goal_relaxation_highest_net_value", tuple(scorecards), None)
    return MetaModeDecision(POLICY_VERSION, "acquire", estimate.mode, score, None,
                            tuple(scorecards), estimate.instruction)
