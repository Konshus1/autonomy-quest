"""Principle mining (slice 2a, BB #764) — candidate causal edges from mission-loop outcomes.

A completed, PRODUCTIVE run joined to its work + learning is evidence that an action
(``work.kind``) had an effect (the mission measure moved). We mine that as a FUZZY causal
edge — low certainty, judgment executor — carrying PROVENANCE (which run/learning produced
it). Mining only ever proposes fuzzy guiding principles; formality is EARNED later by
surprise-driven promotion (BB #746: start fuzzy). Stdlib-only + deterministic.
"""

from __future__ import annotations

from typing import Any

from ralph_portable.causal_edges import edge_identity

_FUZZY_CAP = 0.34


def _effect_label(obs: dict[str, Any]) -> str | None:
    """The direct effect a productive run demonstrates: the mission measure moved."""
    mb, ma = obs.get("measure_before"), obs.get("measure_after")
    if isinstance(mb, (int, float)) and isinstance(ma, (int, float)) and float(ma) != float(mb):
        return "measure_up" if float(ma) > float(mb) else "measure_down"
    return None  # no measurable effect -> not a causal-edge candidate (mining stays honest)


def mine_causal_edges(observations: list[dict[str, Any]], min_confidence: float = 0.0) -> list[dict[str, Any]]:
    """Derive candidate FUZZY causal edges from completed+productive run observations.

    Each observation:
        {work_kind, succeeded, measure_before, measure_after,
         learning_insight, learning_confidence, run_id, work_id, learning_id, scope?}
    Dedups by immutable identity (cause, effect, scope) and ACCUMULATES provenance + support
    across runs that show the same action->effect. Only succeeded runs with a real measure
    move and confidence >= min_confidence become candidates.
    """
    by_id: dict[tuple[str, str, str], dict[str, Any]] = {}
    for obs in observations:
        if not obs.get("succeeded"):
            continue
        cause = str(obs.get("work_kind") or "").strip()
        effect = _effect_label(obs)
        if not cause or not effect:
            continue
        conf = obs.get("learning_confidence")
        conf = float(conf) if isinstance(conf, (int, float)) else 0.0
        if conf < min_confidence:
            continue

        scope = obs.get("scope") or {}
        edge = {
            "cause": cause, "effect": effect, "scope": scope,
            "formality": "fuzzy", "strictness": "advisory", "directness": "judgment",
            "executor": {"kind": "judgment"},
            "predicted_certainty": min(conf, _FUZZY_CAP),
        }
        ident = edge_identity(edge)
        prov = {
            "run_id": obs.get("run_id"), "work_id": obs.get("work_id"),
            "learning_id": obs.get("learning_id"), "insight": obs.get("learning_insight"),
        }
        if ident in by_id:
            e = by_id[ident]
            e["evidence_run_ids"].append(obs.get("run_id"))
            e["provenance"].append(prov)
            e["support_count"] += 1
            e["predicted_certainty"] = min(_FUZZY_CAP, max(e["predicted_certainty"], min(conf, _FUZZY_CAP)))
        else:
            edge["evidence_run_ids"] = [obs.get("run_id")]
            edge["provenance"] = [prov]
            edge["support_count"] = 1
            edge["mined"] = True
            by_id[ident] = edge
    return list(by_id.values())
