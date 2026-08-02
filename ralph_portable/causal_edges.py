"""Causal edges — the front-half of the #746 causal workflow (slice 1a).

A causal claim is a graph edge decomposed into TWO LINKED CLAIMS:
    action -> direct effect         (mechanical; can be a deterministic script)
    direct effect -> mission measure (goal-potency; stays probabilistic)

The edge carries THREE INDEPENDENT WEIGHTS (dials), each separately queryable:
    formality   — how sure the causality is true:      fuzzy | evidential | formal
    strictness  — how hard it binds behavior:          advisory | soft | hard
    directness  — how deterministic the action is:     judgment | predicate | script

Executor slot: primarily an A-actuator (a `script` runs the action deterministically —
the source of a guaranteed action); optionally a `predicate` verifier or a `constraint`.
Surprise-driven learning: each edge carries a PREDICTED CERTAINTY; actual vs predicted =
surprise (ties to ralph_portable.surprise_resolution's ``planning_prediction_surprise``).
Promotion (fuzzy->formal) is GATED; demotion on counter-evidence is FAST; drift re-validates.

Doctrine of record: BB decision #746. Stdlib-only + deterministic, like the rest of
ralph_portable — no DB here (the AGE-backed store is a later slice; this is the contract +
the planning-certainty and surprise math a planner/learning-loop calls).
"""

from __future__ import annotations

import json
from typing import Any

FORMALITY = ("fuzzy", "evidential", "formal")
STRICTNESS = ("advisory", "soft", "hard")
DIRECTNESS = ("judgment", "predicate", "script")
EXECUTOR_KINDS = ("judgment", "predicate", "script", "constraint")

# How much a step is treated as "guaranteed-in-scope": only a formal claim actuated by a
# script clears that bar. Everything else contributes proportional, honest certainty.
_FORMALITY_WEIGHT = {"fuzzy": 0.34, "evidential": 0.67, "formal": 1.0}


def edge_identity(edge: dict[str, Any]) -> tuple[str, str, str]:
    """The IMMUTABLE identity of a causal edge — ``(cause, effect, scope)`` ONLY.

    NEVER bind identity to self-mutating state: formality/directness/strictness (they change
    on promotion/demotion), predicted_certainty, support_count, last_validated, falsified_by,
    or any surprise value. Binding an identity fingerprint to a self-advancing field breaks
    idempotent rerun — the ccf-1557 C2 lesson and the same invariant as
    ``surprise_resolution``'s evidence identity. The dials + certainty + surprise ride as
    mutable EVIDENCE/state keyed by this identity; they are never part of it.
    """
    scope = edge.get("scope") or {}
    scope_key = json.dumps(scope, sort_keys=True) if isinstance(scope, dict) else str(scope)
    return (str(edge.get("cause")), str(edge.get("effect")), scope_key)


def validate_causal_edge(edge: Any) -> list[str]:
    """Validate a causal-edge packet. Returns [] when well-formed."""
    if not isinstance(edge, dict):
        return ["causal_edge must be a mapping"]

    errors: list[str] = []
    for field in ("cause", "effect"):
        if not str(edge.get(field) or "").strip():
            errors.append(f"{field} is required (the action and its direct effect)")

    formality = edge.get("formality")
    if formality not in FORMALITY:
        errors.append(f"formality must be one of {list(FORMALITY)}")
    if edge.get("strictness") not in STRICTNESS:
        errors.append(f"strictness must be one of {list(STRICTNESS)}")
    if edge.get("directness") not in DIRECTNESS:
        errors.append(f"directness must be one of {list(DIRECTNESS)}")

    ex = edge.get("executor")
    if not isinstance(ex, dict) or ex.get("kind") not in EXECUTOR_KINDS:
        errors.append(f"executor must be a mapping with kind in {list(EXECUTOR_KINDS)}")
    else:
        # A script/predicate executor must point at something runnable/checkable.
        if ex["kind"] in ("script", "predicate") and not str(ex.get("ref") or "").strip():
            errors.append(f"executor.kind={ex['kind']!r} requires a 'ref' (task/script/query)")

    pc = edge.get("predicted_certainty")
    if pc is not None and not (isinstance(pc, (int, float)) and 0.0 <= float(pc) <= 1.0):
        errors.append("predicted_certainty must be a number in [0, 1]")

    # Honesty guard: a formal-and-guaranteed claim cannot rest on judgment.
    if formality == "formal" and edge.get("directness") == "judgment":
        errors.append(
            "a formal edge cannot have directness=judgment — a guaranteed action needs a "
            "predicate or script, not an LLM judgment call"
        )
    return errors


def edge_certainty(edge: dict[str, Any]) -> float:
    """The certainty a single (valid) edge lends its effect.

    Uses the edge's stated predicted_certainty when present, otherwise falls back to the
    formality weight. Capped by the formality weight so a fuzzy edge can never claim
    formal-level certainty even if someone set predicted_certainty high.
    """
    cap = _FORMALITY_WEIGHT[edge["formality"]]
    pc = edge.get("predicted_certainty")
    base = float(pc) if isinstance(pc, (int, float)) else cap
    return min(base, cap)


def is_guaranteed(edge: dict[str, Any]) -> bool:
    """Guaranteed-in-scope = a formal claim actuated deterministically (script)."""
    return edge.get("formality") == "formal" and edge.get("directness") == "script"


def plan_certainty(steps: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    """Score a plan against the governing causal edges (slice-1a: read-only check).

    Each step is ``{"action": ..., "effect": ...}``. An edge governs a step when its
    (cause, effect) matches. Returns a CERTAINTY PROFILE — never a binary pass/fail — so a
    plan runs at the certainty its governing edges currently support (BB #746: fuzzy->formal
    is a spectrum). Hard-strictness edges that a plan would VIOLATE are surfaced separately.
    """
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for e in edges:
        if validate_causal_edge(e):
            continue  # ignore malformed edges rather than trust them
        key = (str(e["cause"]), str(e["effect"]))
        # keep the strongest-certainty edge per (cause, effect)
        if key not in by_key or edge_certainty(e) > edge_certainty(by_key[key]):
            by_key[key] = e

    per_step: list[dict[str, Any]] = []
    guaranteed = 0
    covered = 0
    total_certainty = 0.0
    for i, step in enumerate(steps):
        key = (str(step.get("action")), str(step.get("effect")))
        e = by_key.get(key)
        if e is None:
            per_step.append({"step": i, "covered": False, "certainty": 0.0, "guaranteed": False})
            continue
        covered += 1
        c = edge_certainty(e)
        total_certainty += c
        g = is_guaranteed(e)
        guaranteed += 1 if g else 0
        per_step.append({
            "step": i, "covered": True, "certainty": round(c, 4),
            "guaranteed": g, "formality": e["formality"], "strictness": e["strictness"],
            "directness": e["directness"],
        })

    n = len(steps) or 1
    return {
        "steps": len(steps),
        "covered": covered,
        "uncovered": len(steps) - covered,
        "guaranteed_fraction": round(guaranteed / n, 4),
        "mean_certainty": round(total_certainty / n, 4),
        "per_step": per_step,
    }


def _rung(formality: str, direction: int) -> str:
    i = FORMALITY.index(formality)
    return FORMALITY[max(0, min(len(FORMALITY) - 1, i + direction))]


def propose_update(edge: dict[str, Any], surprise_result: dict[str, Any],
                   promote_support: int = 5) -> dict[str, Any]:
    """Given an edge + a surprise, propose a GATED formality change — never apply it.

    Doctrine (BB #746): promotion (toward formal) is GATED and earned by accumulated
    supporting evidence; demotion (on a confident-but-wrong surprise) is proposed FAST. A
    promotion to ``formal`` is refused if the edge's action rests on ``judgment`` (a
    guaranteed claim needs a predicate/script). The caller (learning loop / operator)
    actuates; this returns a proposal only.
    """
    formality = edge.get("formality", "fuzzy")
    signal = surprise_result.get("signal")
    support = int(edge.get("support_count") or 0)

    if signal == "demote":
        target = _rung(formality, -1)
        return {"action": "demote" if target != formality else "hold", "from": formality,
                "to": target, "gated": True, "reason": "confident prediction, wrong outcome"}
    if signal == "confirm" and support >= promote_support:
        target = _rung(formality, +1)
        if target == "formal":
            # RUNG DOCTRINE (BB #775): the evidential->formal rung is minted ONLY by a passing formal
            # oracle proof (which sets executor_verified), NEVER by operational confirms alone, no
            # matter how high support climbs. This keeps "formal = guaranteed" tied to deductive
            # evidence, not statistical repetition. Operational confirms therefore CAP at evidential.
            if edge.get("directness") == "judgment":
                return {"action": "hold", "from": formality, "to": formality, "gated": True,
                        "reason": "cannot promote to formal while directness=judgment (needs script/predicate)"}
            ex = edge.get("executor") or {}
            if not (edge.get("executor_verified") and ex.get("kind") in ("script", "constraint")):
                return {"action": "hold", "from": formality, "to": formality, "gated": True,
                        "reason": "evidential->formal requires a verified formal executor (a passing "
                                  "oracle proof); operational confirms alone cap at evidential (BB #775)"}
        return {"action": "promote" if target != formality else "hold", "from": formality,
                "to": target, "gated": True, "reason": f"{support} supporting observations"}
    return {"action": "hold", "from": formality, "to": formality, "gated": True,
            "reason": "insufficient support or unresolved surprise"}


def surprise(predicted_certainty: float, actual_success: bool | float) -> dict[str, Any]:
    """Prediction-vs-actual = SURPRISE, and the (GATED) learning signal it implies.

    actual_success may be a bool or a [0,1] score. Returns the surprise magnitude and a
    signal — but never auto-applies a weight change: promotion is gated, and even demotion
    is proposed here for the learning loop / surprise_resolution to actuate. Ties to
    ``planning_prediction_surprise``.
    """
    pc = float(predicted_certainty)
    actual = 1.0 if actual_success is True else 0.0 if actual_success is False else float(actual_success)
    if not (0.0 <= pc <= 1.0 and 0.0 <= actual <= 1.0):
        raise ValueError("predicted_certainty and actual_success must be in [0, 1]")

    mag = abs(pc - actual)
    # Signal keys off the OUTCOME (did the causal claim hold?), not prediction magnitude:
    # a success is confirming evidence (supports gated promotion) regardless of how tight the
    # prediction was; a confident-but-failed outcome is a fast-demote signal; an uncertain
    # prediction that failed is inconclusive (held for investigation).
    if actual >= 0.5:
        signal = "confirm"
    elif pc >= 0.7:
        signal = "demote"
    else:
        signal = "investigate"
    return {
        "surprise": round(mag, 4),
        "predicted": round(pc, 4),
        "actual": round(actual, 4),
        "signal": signal,
        "gated": True,             # never auto-applied; the learning loop / operator actuates
        "surprise_type": "planning_prediction_surprise",
    }


def formal_proof_evidence(oracle_result: dict[str, Any]) -> dict[str, Any] | None:
    """Convert an oracle-harness result into an EVIDENCE record for ``record_evidence`` — the SAME
    earned-support channel operational surprises use, so support is only ever written in one place.

    GREEN -> a ``confirm``-typed record (surprise_type=``formal_oracle_proof``, carrying registry_key
    + oracle_digest) — distinguishable in ``evidence[]`` from statistical ``planning_prediction_surprise``.
    RED   -> a ``refute`` record (logged as non-confirming; never increments support).
    ERROR / anything not GREEN|RED -> ``None``: nothing is recorded (fail-closed — uncertainty is
    never a proof). The wrapping store method sets ``executor_verified=True`` only on a GREEN.
    """
    result = oracle_result.get("result")
    if result == "GREEN":
        signal = "confirm"
    elif result == "RED":
        signal = "refute"
    else:
        return None
    # The reason is UNTRUSTED oracle-supplied content (the harness already hard-caps it; cap again
    # here defensively so a reason reaching this function by any path can't smuggle a large payload
    # into the evidence record). It is retained only as a short, oracle-attributed note.
    reason = oracle_result.get("reason")
    reason = str(reason)[:200] if reason is not None else None
    return {
        "signal": signal,
        "gated": True,
        "surprise_type": "formal_oracle_proof",
        "registry_key": oracle_result.get("registry_key"),
        "oracle_digest": oracle_result.get("oracle_digest"),
        "reason_untrusted": reason,  # oracle-attributed, bounded; NOT a trusted field
    }
