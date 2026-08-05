"""Optional curiosity: bounded appetite, never a second mission.

Curiosity is an opt-in exploration drive. It may spend a small standing budget to inspect an
authoritative frontier and stage falsifiable proposals, but it cannot change behavior directly.
Its output goes through the same share -> review -> promote path as any other inert learning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from .config import Measure
from .evaluate import gate4_model_coherence


LOOP_OWNED_SOURCES = (
    "work",
    "runs",
    "learnings",
    "shared_learnings",
    "measurements",
    "heartbeat",
    "hibernation",
)


class CuriosityConfigError(ValueError):
    """Curiosity was enabled without the structural bounds that make it safe."""


@dataclass
class CuriosityDecision:
    run: bool
    reason: str
    budget_cycles_per_day: int = 0


def _finite_positive_decimal(value) -> bool:
    try:
        d = Decimal(str(value))
    except Exception:
        return False
    return d.is_finite() and d > 0


def _finite_positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _referenced_tables(query: str) -> set[str]:
    """Small SQL-shape check for the frontier gate.

    This is not a SQL parser and does not authorize the query. It catches the forbidden shape:
    a frontier seeded from tables the loop itself writes, which would let it define its own scope.
    The database still executes the real query at runtime.

    v1 deliberately catches direct FROM/JOIN references. A view or CTE that launders loop-owned
    rows behind an external name is the operator defeating their own `frontier.authority` claim.
    """
    names = set()
    for match in re.finditer(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)(?:\s+[a-zA-Z_]\w*)?", query, re.I):
        names.add(match.group(1).split(".")[-1].lower())
    return names


def validate_config(curiosity) -> None:
    """Fail loud when opt-in curiosity is missing its bounds.

    Absence is fine. Enabled-but-unbounded is not a warning; it is the same self-certification hole
    as an unscoped mission measure.
    """
    if not curiosity.enabled:
        return

    if not _finite_positive_int(curiosity.budget.cycles_per_day):
        raise CuriosityConfigError("curiosity.enabled requires budget.cycles_per_day > 0")
    if not _finite_positive_decimal(curiosity.budget.max_cost_usd_per_day):
        raise CuriosityConfigError("curiosity.enabled requires budget.max_cost_usd_per_day > 0")

    f = curiosity.frontier
    if not (f.source or "").strip():
        raise CuriosityConfigError("curiosity.enabled requires frontier.source")
    if not (f.authority or "").strip():
        raise CuriosityConfigError("curiosity.enabled requires frontier.authority")
    if (f.target is None and not f.target_query) or f.goal not in ("reach_and_maintain", "maximize"):
        raise CuriosityConfigError(
            "curiosity.enabled requires frontier.target (or target_query) and goal "
            "(reach_and_maintain | maximize)"
        )
    if not _finite_positive_int(f.max_items_per_cycle):
        raise CuriosityConfigError("curiosity.enabled requires frontier.max_items_per_cycle > 0")

    loop_owned = _referenced_tables(f.source) & set(LOOP_OWNED_SOURCES)
    if loop_owned:
        raise CuriosityConfigError(
            "curiosity.frontier.source must be seeded from an external authoritative set, not "
            f"loop-owned table(s): {', '.join(sorted(loop_owned))}"
        )


def exploration_measure(curiosity) -> Measure:
    """Represent the exploration metric with the existing measure helper semantics."""
    f = curiosity.frontier
    return Measure(
        what="curiosity.frontier",
        where=f.source,
        target=f.target,
        target_query=f.target_query,
        goal=f.goal,
    )


def effective_cycles_per_day(curiosity, *, promoted_recent: int, cycles_recent: int) -> int:
    """Ratchet curiosity by promoted evidence, not by self-report.

    Full causal attribution (which exploration moved which mission metric) belongs in a later
    planning layer. The honest proxy here is stricter: if no curiosity-origin staged learning was
    promoted through the behavior-change gate in the ratchet window, curiosity has not earned the
    same appetite and loses budget.
    """
    base = curiosity.budget.cycles_per_day
    if not curiosity.enabled or base <= 0:
        return 0
    if cycles_recent <= 0 or promoted_recent > 0:
        return base
    shrunk = int(base * curiosity.ratchet.shrink_factor)
    return max(curiosity.ratchet.floor_cycles_per_day, shrunk)


def _budget_gate(curiosity, *, spent_today: Decimal, mission_soft_cap_reached: bool,
                 curiosity_cycles_today: int, curiosity_cost_today: Decimal,
                 promoted_recent: int, cycles_recent: int) -> CuriosityDecision:
    """The shared budget/ratchet/cost gate every curiosity trigger passes through (BB #587).

    ONE place where "is there curiosity appetite left today" is answered — the frontier-driven
    path (should_explore) and the incoherence-triggered path (should_probe_incoherence) both
    consume it, so neither can out-spend the shared standing budget and a fix to the gate fixes
    both triggers. Returns a run=True decision (budget_cycles_per_day set) when appetite
    remains, else the refusal decision with its reason.
    """
    if mission_soft_cap_reached:
        return CuriosityDecision(False, "mission cost pressure; curiosity throttled first")

    cycles = effective_cycles_per_day(
        curiosity, promoted_recent=promoted_recent, cycles_recent=cycles_recent)
    if cycles <= 0:
        return CuriosityDecision(False, "curiosity ratchet reduced budget to zero", cycles)
    if curiosity_cycles_today >= cycles:
        return CuriosityDecision(False, "curiosity cycle budget exhausted", cycles)

    max_cost = Decimal(str(curiosity.budget.max_cost_usd_per_day))
    if curiosity_cost_today >= max_cost:
        return CuriosityDecision(False, "curiosity cost budget exhausted", cycles)
    if spent_today < 0:
        return CuriosityDecision(False, "invalid budget state", cycles)

    return CuriosityDecision(True, "budget available", cycles)


def should_probe_incoherence(curiosity, *, predicted_certainty, actual_success: bool,
                             spent_today: Decimal, mission_soft_cap_reached: bool,
                             curiosity_cycles_today: int, curiosity_cost_today: Decimal,
                             promoted_recent: int, cycles_recent: int = 0) -> CuriosityDecision:
    """The incoherence-triggered curiosity gate (BB #2301 / G1): may a world-model contradiction
    stage a probe?

    ADDITIVE to escalation, never a replacement — evaluate's verdict is untouched and the human
    is still paged; this only decides whether curiosity ALSO spends a bounded cycle staging an
    inert proposal on the surprised edge's neighborhood. Curiosity is not a substitute for human
    escalation: if its budget/ratchet has run out, the probe is skipped and the contradiction
    still reaches the human.

    Same budget/ratchet/cost floor as the frontier path (_budget_gate — one place), plus the
    trigger's own defining check: the outcome must actually CONTRADICT the causal model
    (gate4_model_coherence), so a probe can never fire on a coherent cycle no matter what the
    caller believed. Deliberately NO frontier overshoot/satisfaction check here: the coherence
    frontier has no frontier measure. The #587 backlog-overshoot tripwire is the honestly
    deferred gap (BB note #2317) — the probe is bounded by cycles_per_day AND by the real
    incoherence-event rate, itself bounded by the mission cycle rate.
    """
    validate_config(curiosity)
    if not curiosity.enabled:
        return CuriosityDecision(False, "curiosity disabled")
    if gate4_model_coherence(predicted_certainty, actual_success):
        return CuriosityDecision(False, "outcome coherent with the causal model — no incoherence probe")

    gate = _budget_gate(
        curiosity, spent_today=spent_today, mission_soft_cap_reached=mission_soft_cap_reached,
        curiosity_cycles_today=curiosity_cycles_today, curiosity_cost_today=curiosity_cost_today,
        promoted_recent=promoted_recent, cycles_recent=cycles_recent)
    if not gate.run:
        return gate

    return CuriosityDecision(
        True, "incoherence probe: world-model contradiction staged for curiosity",
        gate.budget_cycles_per_day)


def should_explore(curiosity, *, spent_today: Decimal, mission_soft_cap_reached: bool,
                   curiosity_cycles_today: int, curiosity_cost_today: Decimal,
                   promoted_recent: int, cycles_recent: int = 0,
                   frontier_current=None, frontier_target=None) -> CuriosityDecision:
    validate_config(curiosity)
    if not curiosity.enabled:
        return CuriosityDecision(False, "curiosity disabled")

    gate = _budget_gate(
        curiosity, spent_today=spent_today, mission_soft_cap_reached=mission_soft_cap_reached,
        curiosity_cycles_today=curiosity_cycles_today, curiosity_cost_today=curiosity_cost_today,
        promoted_recent=promoted_recent, cycles_recent=cycles_recent)
    if not gate.run:
        return gate
    cycles = gate.budget_cycles_per_day

    m = exploration_measure(curiosity)
    if m.overshooting(frontier_current, frontier_target):
        return CuriosityDecision(False, "curiosity frontier overshoot tripwire", cycles)
    if m.satisfied(frontier_current, frontier_target):
        return CuriosityDecision(False, "curiosity frontier satisfied", cycles)

    return CuriosityDecision(True, "curiosity budget available", cycles)
