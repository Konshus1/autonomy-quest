"""Principle mining (slice 2a, BB #764) — candidate causal edges from mission-loop outcomes.

A completed, PRODUCTIVE run joined to its work + learning is evidence that an action
(``work.kind``) had an effect (the mission measure moved). We mine that as a FUZZY causal
edge — low certainty, judgment executor — carrying PROVENANCE (which run/learning produced
it). Mining only ever proposes fuzzy guiding principles; formality is EARNED later by
surprise-driven promotion (BB #746: start fuzzy). Stdlib-only + deterministic.
"""

from __future__ import annotations

import itertools
import re
from collections import Counter
from typing import Any

from ralph_portable.causal_edges import edge_identity

_FUZZY_CAP = 0.34

_SCHEMA_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "no", "not", "of", "on", "or", "that", "the", "this", "to", "was", "with",
})


def _pattern_tokens(text: Any) -> set[str]:
    """Lowercased word tokens minus stopwords/short words — an edge's relational vocabulary."""
    if not text:
        return set()
    words = re.split(r"[^a-z0-9]+", str(text).lower())
    return {w for w in words if len(w) >= 3 and w not in _SCHEMA_STOPWORDS}


def _edge_signature(edge: dict[str, Any]) -> tuple[set[str], set[str]]:
    """(cause tokens, cause+insight tokens) — the patterns clustering matches on."""
    cause_toks = _pattern_tokens(edge.get("cause"))
    all_toks = set(cause_toks)
    for prov in edge.get("provenance", []):
        all_toks |= _pattern_tokens(prov.get("insight"))
    return cause_toks, all_toks


def cluster_and_tag_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Analogical-compression pass (T3, task #4895 fixture): cluster mined edges by shared
    cause/effect patterns and stamp each with the schema its cluster induces.

    Two edges cluster together when they share a cause token, or share the same effect plus
    any vocabulary (cause or insight) token. Each cluster's abstract schema is its common
    vocabulary — tokens appearing in at least two member edges (a singleton keeps its own
    cause tokens). Every edge then gains:
      * ``tags`` — its cause tokens + effect + the cluster schema (T10's tag-overlap filter
        needs these to pair production-mined edges),
      * ``failure_modes_addressed`` — one cluster-scoped mode (``<schema>_stagnation`` for
        measure_up edges, ``<schema>_regression`` for measure_down),
      * ``formalization_hint`` — "reward <cause>"/"penalize <cause>", deliberately in the
        vocabulary T10's polarity parser reads, so an action mined as BOTH lifting and
        dropping the measure surfaces as a polarity conflict.
    Mutates and returns ``edges``. Deterministic, stdlib-only.
    """
    if not edges:
        return edges

    sigs = [_edge_signature(e) for e in edges]
    parent = list(range(len(edges)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in itertools.combinations(range(len(edges)), 2):
        cause_i, all_i = sigs[i]
        cause_j, all_j = sigs[j]
        same_effect = edges[i].get("effect") == edges[j].get("effect")
        if (cause_i & cause_j) or (same_effect and (all_i & all_j)):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)  # smallest index wins: deterministic roots

    clusters: dict[int, list[int]] = {}
    for i in range(len(edges)):
        clusters.setdefault(find(i), []).append(i)

    for members in clusters.values():
        counts: Counter[str] = Counter()
        for i in members:
            counts.update(sigs[i][1])
        if len(members) > 1:
            schema = sorted(t for t, c in counts.items() if c >= 2)[:6]
        else:
            schema = sorted(sigs[members[0]][0])[:6]
        fm_key = "_".join(schema[:2]) or "measure"
        for i in members:
            edge = edges[i]
            cause = str(edge.get("cause") or "")
            effect = str(edge.get("effect") or "")
            down = effect == "measure_down"
            edge["tags"] = sorted(sigs[i][0] | set(schema) | ({effect} if effect else set()))
            edge["failure_modes_addressed"] = [f"{fm_key}_{'regression' if down else 'stagnation'}"]
            edge["formalization_hint"] = (
                f"{'penalize' if down else 'reward'} {cause} — mined fuzzy evidence it moves "
                f"the mission measure {'down' if down else 'up'} "
                f"(schema: {', '.join(schema) if schema else 'none'})"
            )
    return edges


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
    return cluster_and_tag_edges(edges)
