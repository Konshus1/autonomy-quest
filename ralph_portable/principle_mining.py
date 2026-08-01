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
    Dedups by immutable identity (cause, effect, scope). Within an identity it also dedups by
    ``run_id`` — a single run that recorded several learnings is ONE supporting run, not N (that
    join is many-to-one). Each distinct run contributes one provenance entry (its highest-
    confidence learning).

    Mining deliberately does NOT set ``support_count``: promotion support is EARNED later via the
    surprise loop (``record_evidence`` counts confirms), and a store upsert preserves that earned
    value only if the miner leaves the field alone (BB #746 — support is earned, not observed).
    The miner reports observation BREADTH as ``observed_runs`` (count of distinct supporting runs)
    for display, kept separate from the earned promotion counter. Only succeeded runs with a real
    measure move and confidence >= min_confidence become candidates.
    """
    # ident -> {edge, runs: {run_id: {prov, conf}}, max_conf}
    acc: dict[tuple[str, str, str], dict[str, Any]] = {}
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
        ident = edge_identity({"cause": cause, "effect": effect, "scope": scope})
        entry = acc.get(ident)
        if entry is None:
            entry = {
                "edge": {
                    "cause": cause, "effect": effect, "scope": scope,
                    "formality": "fuzzy", "strictness": "advisory", "directness": "judgment",
                    "executor": {"kind": "judgment"},
                    "predicted_certainty": min(conf, _FUZZY_CAP),
                    "mined": True,
                },
                "runs": {},
                "max_conf": conf,
            }
            acc[ident] = entry
        entry["max_conf"] = max(entry["max_conf"], conf)
        run_id = obs.get("run_id")
        prov = {
            "run_id": run_id, "work_id": obs.get("work_id"),
            "learning_id": obs.get("learning_id"), "insight": obs.get("learning_insight"),
        }
        prior = entry["runs"].get(run_id)
        if prior is None or conf > prior["conf"]:  # one provenance per run: keep its best learning
            entry["runs"][run_id] = {"prov": prov, "conf": conf}

    edges: list[dict[str, Any]] = []
    for entry in acc.values():
        edge = entry["edge"]
        run_ids = sorted(entry["runs"].keys(), key=lambda r: (r is None, r))  # deterministic, None last
        edge["provenance"] = [entry["runs"][r]["prov"] for r in run_ids]
        edge["evidence_run_ids"] = run_ids  # distinct, no duplicates
        edge["observed_runs"] = len(run_ids)
        edge["predicted_certainty"] = min(_FUZZY_CAP, entry["max_conf"])
        edges.append(edge)
    return edges
