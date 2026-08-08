"""T10 Conceptual-Inconsistency Detector — the "Maxwell-vs-Newton detector" (C4b/DR4).

Detects contradictions BETWEEN causal principles without waiting for an outcome
failure. Einstein's 1907 "happiest thought" was triggered by a THEORY-INTERNAL
contradiction (Maxwell's constant-light-speed vs Newton's Galilean relativity),
not by a failed experiment. This module scans the principle corpus for those
internal contradictions.

Three-phase pipeline (cost-ascending):
  Phase 1 — Deterministic tag-overlap filter (zero LLM cost).
  Phase 2 — LLM-assisted conflict classification (gateway, prompt_label).
  Phase 3 — Emit ralph_surprise_packet_v0 with surprise_type="conceptual_inconsistency".

READ-ONLY: the detector flags; it never demotes, merges, or edits principles.
Resolution is a separate gated step (DR3/DR6).

Origin: Jump-system gap analysis BB note #2358 (C4b = BIG GAP).
Design spec: artifacts/task_4834/t10_conceptual_inconsistency_detector_design.md
Fixture: artifacts/task_4902/phase1_runner.py (proven, 0 conflicts on 15-principle corpus)
"""

from __future__ import annotations

import itertools
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("aq.t10")

# ── Phase 1: Deterministic tag-overlap filter ──────────────────────────────

TAG_OVERLAP_THRESHOLD = 2
FAILURE_MODE_OVERLAP_THRESHOLD = 1


@dataclass
class CandidatePair:
    """A pair of principles that share enough surface area to warrant LLM classification."""
    principle_a: str
    principle_b: str
    tag_overlap: int
    failure_mode_overlap: int
    polarity_conflict: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "principle_a": self.principle_a,
            "principle_b": self.principle_b,
            "tag_overlap": self.tag_overlap,
            "failure_mode_overlap": self.failure_mode_overlap,
            "polarity_conflict": self.polarity_conflict,
            "reason": self.reason,
        }


def parse_hint_polarity(hint: str) -> dict[str, Any]:
    """Parse a formalization_hint for action references and their polarity.

    Returns {actions: [{action, polarity}]} where polarity is 'reward' or 'penalty'/'constraint'.
    """
    if not hint:
        return {"actions": []}

    actions = []
    hint_lower = hint.lower()

    # Look for action patterns: "penalize X", "reward X", "require X", "flag X"
    # Also ASP-style: ":- action(...)" (constraint), "maximize action(...)" (reward)
    for match in re.finditer(
        r'(?:penalize|penalises?|penalty_for|prevent|discourage|constraint_on|prohibit)\s+(\w+)',
        hint_lower
    ):
        actions.append({"action": match.group(1), "polarity": "penalty"})

    for match in re.finditer(
        r'(?:reward|prefer|promote|encourage|maximize|allow)\s+(\w+)',
        hint_lower
    ):
        actions.append({"action": match.group(1), "polarity": "reward"})

    # ASP constraint syntax: ":- action(...)" is a hard constraint (penalty)
    for match in re.finditer(r':-\s*(\w+)\s*\(', hint_lower):
        actions.append({"action": match.group(1), "polarity": "penalty"})

    return {"actions": actions}


def check_polarity_conflict(hint_a: str, hint_b: str) -> dict[str, Any]:
    """Check if two hints reward and penalize the same action."""
    pa = parse_hint_polarity(hint_a)
    pb = parse_hint_polarity(hint_b)

    actions_a = {a["action"]: a["polarity"] for a in pa["actions"]}
    actions_b = {b["action"]: b["polarity"] for b in pb["actions"]}

    conflicts = []
    for action in set(actions_a) & set(actions_b):
        if actions_a[action] != actions_b[action]:
            conflicts.append({
                "action": action,
                "polarity_a": actions_a[action],
                "polarity_b": actions_b[action],
            })

    return {"has_conflict": len(conflicts) > 0, "conflicts": conflicts}


def phase1_deterministic_filter(principles: list[dict[str, Any]]) -> list[CandidatePair]:
    """Phase 1: deterministic tag-overlap filter. Zero LLM cost.

    For every pair of active principles, compute tag_overlap and failure_mode_overlap.
    Candidate pairs: tag_overlap >= 2 OR failure_mode_overlap >= 1 OR polarity conflict.
    """
    active = [p for p in principles if p.get("is_active", True) and p.get("status") in ("provisional", "active")]
    candidates: list[CandidatePair] = []

    for a, b in itertools.combinations(active, 2):
        tags_a = set(a.get("tags", []))
        tags_b = set(b.get("tags", []))
        tag_overlap = len(tags_a & tags_b)

        fm_a = set(a.get("failure_modes_addressed", []))
        fm_b = set(b.get("failure_modes_addressed", []))
        fm_overlap = len(fm_a & fm_b)

        polarity = check_polarity_conflict(
            a.get("formalization_hint", ""),
            b.get("formalization_hint", ""),
        )

        reasons = []
        if tag_overlap >= TAG_OVERLAP_THRESHOLD:
            reasons.append(f"tag_overlap={tag_overlap}")
        if fm_overlap >= FAILURE_MODE_OVERLAP_THRESHOLD:
            reasons.append(f"failure_mode_overlap={fm_overlap}")
        if polarity["has_conflict"]:
            reasons.append("polarity_conflict")

        if reasons:
            candidates.append(CandidatePair(
                principle_a=a.get("principle_id", str(a.get("id", "?"))),
                principle_b=b.get("principle_id", str(b.get("id", "?"))),
                tag_overlap=tag_overlap,
                failure_mode_overlap=fm_overlap,
                polarity_conflict=polarity["has_conflict"],
                reason=", ".join(reasons),
            ))

    return candidates


# ── Phase 2: LLM-assisted classification ────────────────────────────────────

CONFLICT_CLASSIFICATIONS = ("direct_conflict", "guidance_conflict", "soft_tension", "no_conflict")


def phase2_classify_pair(
    principle_a: dict[str, Any],
    principle_b: dict[str, Any],
    llm_classify_fn=None,
) -> dict[str, Any]:
    """Phase 2: classify a candidate pair using an LLM or a fallback heuristic.

    llm_classify_fn: callable(prompt: str) -> str, or None to use heuristic classification.
    In production this should be app.llm_gateway.chat_complete with
    prompt_label="conceptual_inconsistency_detector".

    The LLM classifies and explains; it does NOT demote or mutate.
    """
    if llm_classify_fn is not None:
        prompt = _build_classification_prompt(principle_a, principle_b)
        try:
            raw = llm_classify_fn(prompt)
            classification = _parse_llm_classification(raw)
            return classification
        except Exception as exc:
            log.warning("LLM classification failed for %s/%s: %s — using heuristic",
                        principle_a.get("principle_id"), principle_b.get("principle_id"), exc)

    # Heuristic fallback: check formalization hints for direct polarity conflicts
    return _heuristic_classify(principle_a, principle_b)


def _build_classification_prompt(a: dict[str, Any], b: dict[str, Any]) -> str:
    return f"""Given these two causal principles with their tags and failure-modes-addressed,
do they give CONFLICTING planning guidance in any overlapping scope?

Principle A ({a.get('principle_id', '?')}):
  Text: {a.get('principle_text', '')}
  Tags: {a.get('tags', [])}
  Failure modes addressed: {a.get('failure_modes_addressed', [])}
  Formalization hint: {a.get('formalization_hint', '')}

Principle B ({b.get('principle_id', '?')}):
  Text: {b.get('principle_text', '')}
  Tags: {b.get('tags', [])}
  Failure modes addressed: {b.get('failure_modes_addressed', [])}
  Formalization hint: {b.get('formalization_hint', '')}

Classify as one of: direct_conflict, guidance_conflict, soft_tension, no_conflict.
- direct_conflict: A says X causes Y and B says X prevents Y in overlapping scope.
- guidance_conflict: A and B address the same failure mode but promote opposing approaches.
- soft_tension: A and B share tags/scope but give only mildly opposing guidance.
- no_conflict: no meaningful conflict.

If conflict, state the overlapping scope and what each predicts.
Reply as JSON: {{"classification": "...", "scope": "...", "explanation": "..."}}
"""


def _parse_llm_classification(raw: str) -> dict[str, Any]:
    """Parse the LLM's classification reply."""
    # Try JSON parse first
    try:
        result = json.loads(raw)
        classification = result.get("classification", "no_conflict")
        if classification not in CONFLICT_CLASSIFICATIONS:
            classification = "no_conflict"
        return {
            "classification": classification,
            "scope": result.get("scope", ""),
            "explanation": result.get("explanation", ""),
        }
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: scan for classification keywords
    raw_lower = raw.lower()
    for cls in CONFLICT_CLASSIFICATIONS:
        if cls in raw_lower:
            return {"classification": cls, "scope": "", "explanation": raw[:300]}

    return {"classification": "no_conflict", "scope": "", "explanation": "unparseable"}


def _heuristic_classify(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Heuristic classification when no LLM is available (fixture-grade).

    NOTE: This is a CANDIDATE signal, not a verdict. The heuristic cannot
    detect opposing approaches -- it flags pairs for LLM review. To avoid
    false conflicts, we check polarity: if both hints have the SAME polarity
    on the same action, that's agreement, not conflict.
    """
    polarity = check_polarity_conflict(
        a.get("formalization_hint", ""),
        b.get("formalization_hint", ""),
    )
    if polarity["has_conflict"]:
        return {
            "classification": "direct_conflict",
            "scope": f"action: {polarity['conflicts'][0]['action']}",
            "explanation": f"Formalization hints have opposing polarity on {polarity['conflicts'][0]['action']}",
        }

    fm_a = set(a.get("failure_modes_addressed", []))
    fm_b = set(b.get("failure_modes_addressed", []))
    shared_fm = fm_a & fm_b
    if shared_fm:
        # Check polarity: if both hints reward the same action, it's agreement
        # not conflict. Only flag as guidance_conflict if polarity differs.
        pa = parse_hint_polarity(a.get("formalization_hint", ""))
        pb = parse_hint_polarity(b.get("formalization_hint", ""))
        actions_a = {a["action"]: a["polarity"] for a in pa["actions"]}
        actions_b = {b["action"]: b["polarity"] for b in pb["actions"]}
        shared_actions = set(actions_a) & set(actions_b)
        has_opposing = any(actions_a[a] != actions_b[a] for a in shared_actions)
        
        if has_opposing:
            return {
                "classification": "guidance_conflict",
                "scope": f"failure_modes: {shared_fm}",
                "explanation": "Both address the same failure mode with opposing approaches",
            }
        elif shared_actions:
            # Same polarity on shared actions = agreement, not conflict
            return {
                "classification": "no_conflict",
                "scope": "",
                "explanation": "Shared failure modes but same polarity — agreement, not conflict",
            }
        else:
            # Shared failure modes but no formalization overlap — flag for LLM review
            return {
                "classification": "soft_tension",
                "scope": f"failure_modes: {shared_fm}",
                "explanation": "Shared failure modes — check for opposing remedies (needs LLM review)",
            }

    tags_a = set(a.get("tags", []))
    tags_b = set(b.get("tags", []))
    if len(tags_a & tags_b) >= 3:
        return {
            "classification": "soft_tension",
            "scope": f"tags: {tags_a & tags_b}",
            "explanation": "High tag overlap — possible tension worth monitoring",
        }

    return {"classification": "no_conflict", "scope": "", "explanation": ""}


# ── Phase 3: Emit surprise packet ───────────────────────────────────────────

SEVERITY_MAP = {
    "direct_conflict": "high",
    "guidance_conflict": "medium",
    "soft_tension": "low",
    "no_conflict": "info",
}


def phase3_emit_packet(
    principle_a: dict[str, Any],
    principle_b: dict[str, Any],
    classification: dict[str, Any],
) -> dict[str, Any] | None:
    """Phase 3: emit a ralph_surprise_packet_v0 with surprise_type="conceptual_inconsistency".

    Returns None for no_conflict (no packet emitted).
    """
    cls = classification.get("classification", "no_conflict")
    if cls == "no_conflict":
        return None

    severity = SEVERITY_MAP.get(cls, "info")

    return {
        "packet_type": "ralph_surprise_packet_v0",
        "schema_version": "0.1.0",
        "surprise_type": "conceptual_inconsistency",
        "severity": severity,
        "expected_outcome": principle_a.get("principle_text", ""),
        "actual_outcome": principle_b.get("principle_text", ""),
        "evidence_refs": [
            principle_a.get("principle_id", str(principle_a.get("id", "?"))),
            principle_b.get("principle_id", str(principle_b.get("id", "?"))),
        ],
        "likely_causal_hypotheses": [
            {
                "principle_id": principle_a.get("principle_id"),
                "formalization_hint": principle_a.get("formalization_hint", ""),
            },
            {
                "principle_id": principle_b.get("principle_id"),
                "formalization_hint": principle_b.get("formalization_hint", ""),
            },
        ],
        "proposed_model_update": "investigate: which principle should be demoted, scoped, or merged?",
        "classification": cls,
        "scope": classification.get("scope", ""),
        "explanation": classification.get("explanation", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Full pipeline ───────────────────────────────────────────────────────────

def scan_inconsistencies(
    principles: list[dict[str, Any]],
    llm_classify_fn=None,
) -> dict[str, Any]:
    """Run the full T10 inconsistency-detection pipeline.

    Returns:
      {
        "total_principles": int,
        "total_pairs": int,
        "candidate_pairs": int,
        "classifications": [{"pair": ..., "classification": ...}],
        "surprise_packets": [packet, ...],
        "pruned_pct": float,
      }
    """
    n = len(principles)
    total_pairs = n * (n - 1) // 2 if n > 1 else 0

    # Phase 1: deterministic filter
    candidates = phase1_deterministic_filter(principles)
    pruned_pct = ((total_pairs - len(candidates)) / total_pairs * 100) if total_pairs > 0 else 0

    log.info("T10 Phase 1: %d principles -> %d candidate pairs (%.0f%% pruned)",
             n, len(candidates), pruned_pct)

    # Build lookup for Phase 2
    by_id = {}
    for p in principles:
        pid = p.get("principle_id", str(p.get("id", "?")))
        by_id[pid] = p

    # Phase 2 + 3: classify each candidate and emit packets
    classifications = []
    packets = []

    for cand in candidates:
        pa = by_id.get(cand.principle_a, {})
        pb = by_id.get(cand.principle_b, {})

        result = phase2_classify_pair(pa, pb, llm_classify_fn)
        result["pair"] = [cand.principle_a, cand.principle_b]
        result["reason"] = cand.reason
        classifications.append(result)

        packet = phase3_emit_packet(pa, pb, result)
        if packet is not None:
            packets.append(packet)
            log.warning("T10: %s conflict found between %s and %s — scope: %s",
                        result["classification"], cand.principle_a, cand.principle_b,
                        result.get("scope", ""))

    return {
        "total_principles": n,
        "total_pairs": total_pairs,
        "candidate_pairs": len(candidates),
        "classifications": classifications,
        "surprise_packets": packets,
        "pruned_pct": round(pruned_pct, 1),
        "conflicts_found": len(packets),
    }
