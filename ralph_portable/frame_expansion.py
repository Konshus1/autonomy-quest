"""T11 Frame-Expansion Mechanism — the "no category for this" detector + dimension inductor (C10/DR5).

When the system encounters something that doesn't fit any existing descriptive
dimension, mapping fails silently — it either forces a bad fit (precedent-
recombination) or abstains. This module adds a third option: NOTICE the gap,
propose a new dimension to accommodate it, and gate the promotion.

Three parts:
  Part A — Detection: mapping_exhausted when structural mapping fails on
           uncapped attributes (no existing dimension captures it).
  Part B — Proposal: recurring mismatch -> LLM-assisted candidate dimension proposal.
  Part C — Gate: candidate stays status="proposed" (NO auto-promotion).

T10 and T11 are a PAIR (per BB #2358): T10 triggers the jump (contradiction ->
"need a new principle"); T11 enables it (expand frame -> new dimensions -> new
analogies). Without both, the system can only recombine within its current frame.

Two modes (Kevin 2026-08-07, decision #830):
  Mode 1 — Situation-driven: automatic, triggered by mapping_exhausted when the
           task's conditions require a dimension the frame doesn't have.
  Mode 2 — Curiosity-driven: the system's self-improvement loop looks around,
           judges that frame expansion MIGHT be useful. Exploratory, conservative.

Origin: Jump-system gap analysis BB note #2358 (C10 = BIG GAP, zero code).
Design spec: artifacts/task_4834/t11_frame_expansion_mechanism_design.md
Fixture: artifacts/task_4903/run_fixture.py (proven, both modes pass)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("aq.t11")

# ── Dimension library ──────────────────────────────────────────────────────

DEFAULT_DIMENSIONS = [
    {"id": "identity", "definition": "The unique identifier or canonical label.", "facet": "semantic"},
    {"id": "failure_modes", "definition": "The kinds of ways the concept can fail.", "facet": "causal"},
    {"id": "state_transitions", "definition": "The allowed changes between states.", "facet": "causal"},
    {"id": "purpose", "definition": "The objective or role the concept serves.", "facet": "semantic"},
    {"id": "dependencies", "definition": "Prerequisites that must be satisfied.", "facet": "relational"},
    {"id": "constraints", "definition": "Rules or limits that restrict usage.", "facet": "functional"},
    {"id": "inputs", "definition": "Required starting conditions.", "facet": "relational"},
    {"id": "outputs", "definition": "The deliverables or results produced.", "facet": "functional"},
    {"id": "idempotency", "definition": "Whether repeating leaves outcome unchanged.", "facet": "causal"},
    {"id": "recovery_actions", "definition": "Fallback or remediation after failure.", "facet": "functional"},
    # Dimensions from T1's 54-dim core that showed high coverage
    {"id": "cost", "definition": "Resource expenditure (time, money, compute).", "facet": "economic"},
    {"id": "risk", "definition": "Probability and magnitude of negative outcome.", "facet": "economic"},
    {"id": "reversibility", "definition": "Whether an action can be undone and the cost of doing so.", "facet": "causal"},
    {"id": "blast_radius", "definition": "The scope of impact if the action goes wrong.", "facet": "causal"},
    {"id": "speed_to_validation", "definition": "How quickly you learn if it worked.", "facet": "temporal"},
    {"id": "strategic_leverage", "definition": "How much this unblocks vs other work.", "facet": "strategic"},
    {"id": "learning_value", "definition": "How much this teaches about the system.", "facet": "epistemic"},
    {"id": "business_value", "definition": "Direct contribution to revenue or mission.", "facet": "economic"},
    {"id": "user_value", "definition": "Value delivered to end users.", "facet": "economic"},
    {"id": "reliability_impact", "definition": "Effect on system stability and uptime.", "facet": "operational"},
    {"id": "dependency_unblocking", "definition": "Whether this removes a blocker for others.", "facet": "relational"},
]


class DimensionLibrary:
    """In-memory dimension library. In production, backed by a DB table."""

    def __init__(self, dimensions: list[dict[str, Any]] | None = None):
        self._dims = {d["id"]: d for d in (dimensions or DEFAULT_DIMENSIONS)}
        self._candidates: list[dict[str, Any]] = []

    def all(self) -> list[dict[str, Any]]:
        return list(self._dims.values())

    def get(self, dim_id: str) -> dict[str, Any] | None:
        return self._dims.get(dim_id)

    def candidates(self) -> list[dict[str, Any]]:
        return list(self._candidates)

    def add_candidate(self, candidate: dict[str, Any]) -> None:
        """Add a proposed dimension. Does NOT promote it into the active library."""
        candidate.setdefault("status", "proposed")
        candidate.setdefault("proposed_at", datetime.now(timezone.utc).isoformat())
        self._candidates.append(candidate)

    def promote(self, candidate_id: str, reason: str) -> dict[str, Any] | None:
        """Promote a candidate to the active library. Manager-gated."""
        for c in self._candidates:
            if c.get("id") == candidate_id:
                c["status"] = "active"
                c["promoted_at"] = datetime.now(timezone.utc).isoformat()
                c["promotion_reason"] = reason
                self._dims[c["id"]] = {k: v for k, v in c.items() if k not in ("status", "proposed_at")}
                self._dims[c["id"]]["status"] = "active"
                return c
        return None

    def find_best_match(self, attribute: str) -> dict[str, Any] | None:
        """Find the best-matching dimension for an attribute name.

        Checks: (1) direct ID match, (2) ID substring match, (3) word overlap.
        In production this would use embeddings (T2's lens_analogy.py does this
        with text embeddings — this keyword fallback is the fixture-grade version).
        """
        attr_lower = attribute.lower().replace("_", " ").strip()
        attr_compact = attribute.lower().replace("_", "").replace(" ", "").strip()

        # 1. Direct ID match
        for dim_id, dim in self._dims.items():
            if attribute.lower() == dim_id:
                return {"dimension": dim, "similarity": 1.0}

        # 2. ID substring / compact match
        for dim_id, dim in self._dims.items():
            dim_compact = dim_id.replace("_", "").replace(" ", "")
            if attr_compact and dim_compact and (
                attr_compact in dim_compact or dim_compact in attr_compact
            ):
                return {"dimension": dim, "similarity": 0.9}

        # 3. Word overlap between attribute and dimension (id + definition)
        best = None
        best_score = 0.0
        attr_words = set(attr_lower.split())

        for dim in self._dims.values():
            dim_text = f"{dim['id']} {dim.get('definition', '')}".lower()
            dim_words = set(dim_text.split())
            if not attr_words:
                continue
            overlap = len(attr_words & dim_words)
            score = overlap / max(len(attr_words), 1)
            if score > best_score:
                best_score = score
                best = dim

        return {"dimension": best, "similarity": best_score} if best else None


# ── Part A: Detection — mapping_exhausted signal ───────────────────────────

MAPPING_EXHAUST_THRESHOLD = 0.68  # Below this similarity, attribute is "uncapped"


@dataclass
class MappingExhausted:
    """Signal emitted when structural mapping fails on uncapped attributes."""
    episode_id: str
    uncapped_attributes: list[dict[str, Any]]  # [{attribute, best_match_dim, best_sim}]
    episode_graph: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "uncapped_attributes": self.uncapped_attributes,
            "signal": "mapping_exhausted",
        }


def detect_mapping_exhausted(
    episode: dict[str, Any],
    library: DimensionLibrary,
    threshold: float = MAPPING_EXHAUST_THRESHOLD,
) -> MappingExhausted | None:
    """Part A: detect mapping_exhausted for an episode.

    An episode has attributes (e.g. from a learning's relational graph). For each
    attribute, find the best-matching dimension. If ALL attributes map above
    threshold, mapping succeeds. If any attribute has no match above threshold,
    mapping_exhausted fires with the specific uncapped attributes.

    Returns None if mapping succeeds (all attributes capped).
    """
    attributes = episode.get("attributes", [])
    if not attributes:
        return None

    uncapped = []
    for attr in attributes:
        match = library.find_best_match(attr)
        if match is None or match["similarity"] < threshold:
            uncapped.append({
                "attribute": attr,
                "best_match_dim": match["dimension"]["id"] if match and match["dimension"] else None,
                "best_similarity": match["similarity"] if match else 0.0,
            })

    if not uncapped:
        return None  # mapping succeeded

    return MappingExhausted(
        episode_id=episode.get("episode_id", episode.get("id", "?")),
        uncapped_attributes=uncapped,
        episode_graph=episode.get("relational_graph"),
    )


# ── Part B: Proposal — candidate dimension generation ──────────────────────

RECURRENCE_THRESHOLD = 2  # Same uncapped attribute in >= 2 episodes


def accumulate_mismatches(
    signals: list[MappingExhausted],
) -> dict[str, list[str]]:
    """Accumulate recurring mismatches across multiple mapping_exhausted signals.

    Returns {attribute: [episode_ids]} for attributes that appear in >= threshold episodes.
    """
    attr_episodes: dict[str, list[str]] = {}
    for signal in signals:
        for uncapped in signal.uncapped_attributes:
            attr = uncapped["attribute"]
            attr_episodes.setdefault(attr, []).append(signal.episode_id)

    # Filter to recurring only
    recurring = {
        attr: eps for attr, eps in attr_episodes.items()
        if len(eps) >= RECURRENCE_THRESHOLD
    }
    return recurring


def propose_dimension(
    attribute: str,
    mismatch_count: int,
    source_episodes: list[str],
    llm_propose_fn=None,
) -> dict[str, Any]:
    """Part B: generate a candidate dimension proposal for a recurring mismatch.

    llm_propose_fn: callable(prompt: str) -> str, or None for heuristic proposal.
    In production this should be app.llm_gateway.chat_complete with
    prompt_label="frame_expansion_dimension_proposer".
    """
    if llm_propose_fn is not None:
        prompt = _build_proposal_prompt(attribute, mismatch_count, source_episodes)
        try:
            raw = llm_propose_fn(prompt)
            return _parse_llm_proposal(raw, attribute, source_episodes)
        except Exception as exc:
            log.warning("LLM dimension proposal failed for %s: %s — using heuristic", attribute, exc)

    # Heuristic proposal
    return _heuristic_propose(attribute, mismatch_count, source_episodes)


def _build_proposal_prompt(attr: str, count: int, episodes: list[str]) -> str:
    return f"""A new descriptive dimension is needed.

The attribute "{attr}" has appeared in {count} episodes ({', '.join(episodes[:5])})
but no existing dimension in the library captures it.

Propose a new descriptive dimension that would accommodate this recurring attribute.
Reply as JSON:
{{
  "id": "snake_case_name",
  "definition": "What this dimension measures (1-2 sentences)",
  "facet": "causal|semantic|relational|functional|economic|temporal|strategic|epistemic|operational",
  "examples": ["example concept 1", "example concept 2"]
}}
"""


def _parse_llm_proposal(raw: str, attr: str, episodes: list[str]) -> dict[str, Any]:
    """Parse the LLM's dimension proposal."""
    try:
        result = json.loads(raw)
        result.setdefault("id", attr.replace(" ", "_"))
        result.setdefault("source_attribute", attr)
        result["source_episodes"] = episodes
        result["recurrence_count"] = len(episodes)
        return result
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback
    return _heuristic_propose(attr, len(episodes), episodes)


def _heuristic_propose(attr: str, count: int, episodes: list[str]) -> dict[str, Any]:
    """Heuristic dimension proposal when no LLM is available."""
    dim_id = attr.lower().replace(" ", "_").replace("-", "_")
    return {
        "id": dim_id,
        "definition": f"The extent to which a concept exhibits {attr.replace('_', ' ')}.",
        "facet": "causal",
        "source_attribute": attr,
        "source_episodes": episodes,
        "recurrence_count": count,
        "heuristic": True,
    }


# ── Part C: Gate — manager-gated promotion ─────────────────────────────────

def gate_candidate(
    candidate: dict[str, Any],
    library: DimensionLibrary,
    held_out_episodes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Part C: evaluate a candidate dimension for promotion readiness.

    A candidate enters the active library ONLY through:
    1. Recurrence >= threshold (checked in Part B)
    2. Adding it improves mapping on held-out episodes (checked here)
    3. No existing dimension subsumes it (checked here)
    4. Manager approval (NOT granted here — DR12 forbids LLM-only gating)

    Returns a gate report. The candidate is added to the library as status="proposed"
    but NOT promoted. Promotion requires a separate manager call.
    """
    # Check subsumption
    best_match = library.find_best_match(candidate.get("id", ""))
    subsumed = best_match and best_match["similarity"] >= MAPPING_EXHAUST_THRESHOLD

    # Check held-out improvement
    improves_mapping = True  # placeholder; real check would run mapping with/without
    if held_out_episodes:
        improves_mapping = _check_held_out_improvement(candidate, library, held_out_episodes)

    gate_report = {
        "candidate_id": candidate.get("id"),
        "recurrence_met": candidate.get("recurrence_count", 0) >= RECURRENCE_THRESHOLD,
        "subsumed_by": best_match["dimension"]["id"] if subsumed and best_match else None,
        "improves_held_out": improves_mapping,
        "manager_approval": False,  # NEVER auto-granted
        "ready_for_promotion": (
            candidate.get("recurrence_count", 0) >= RECURRENCE_THRESHOLD
            and not subsumed
            and improves_mapping
        ),
        "note": "Manager approval required (DR12). Candidate is proposed, not promoted.",
    }

    # Add as candidate (status="proposed")
    library.add_candidate(candidate)

    return gate_report


def _check_held_out_improvement(
    candidate: dict[str, Any],
    library: DimensionLibrary,
    held_out: list[dict[str, Any]],
) -> bool:
    """Check if adding the candidate dimension improves mapping on held-out episodes.

    For each held-out episode, check if any attribute that was previously uncapped
    is now capped by the candidate dimension.
    """
    improved = 0
    for episode in held_out:
        signal = detect_mapping_exhausted(episode, library)
        if signal is None:
            continue
        # Check if the candidate would cap any of the uncapped attributes
        for uncapped in signal.uncapped_attributes:
            attr = uncapped["attribute"]
            # Simple check: does the candidate's id match the attribute?
            if candidate.get("id", "").replace("_", " ") in attr.replace("_", " "):
                improved += 1
                break

    return improved > 0


# ── Full pipeline ───────────────────────────────────────────────────────────

def run_frame_expansion(
    episodes: list[dict[str, Any]],
    library: DimensionLibrary | None = None,
    llm_propose_fn=None,
    mode: str = "situation_driven",
) -> dict[str, Any]:
    """Run the full T11 frame-expansion pipeline.

    mode: "situation_driven" (Mode 1) or "curiosity_driven" (Mode 2).
    Mode 1: triggered by mapping_exhausted on real episodes.
    Mode 2: the system proactively looks for frame gaps (same mechanism, different trigger).

    Returns:
      {
        "mode": str,
        "episodes_scanned": int,
        "mapping_exhausted_signals": [...],
        "recurring_mismatches": {...},
        "proposed_dimensions": [...],
        "gate_reports": [...],
        "promoted": 0,  # always 0 — promotion is manager-gated
      }
    """
    lib = library or DimensionLibrary()

    # Part A: detect mapping_exhausted
    signals = []
    for ep in episodes:
        signal = detect_mapping_exhausted(ep, lib)
        if signal is not None:
            signals.append(signal)

    log.info("T11 Part A: %d episodes scanned, %d mapping_exhausted signals",
             len(episodes), len(signals))

    # Part B: accumulate recurring mismatches and propose dimensions
    recurring = accumulate_mismatches(signals)
    proposals = []
    for attr, eps in recurring.items():
        proposal = propose_dimension(attr, len(eps), eps, llm_propose_fn)
        proposals.append(proposal)
        log.info("T11 Part B: proposed dimension '%s' for recurring attribute '%s' (%d episodes)",
                 proposal.get("id"), attr, len(eps))

    # Part C: gate each candidate
    gate_reports = []
    for proposal in proposals:
        report = gate_candidate(proposal, lib)
        gate_reports.append(report)

    return {
        "mode": mode,
        "episodes_scanned": len(episodes),
        "mapping_exhausted_signals": [s.to_dict() for s in signals],
        "recurring_mismatches": dict(recurring),
        "proposed_dimensions": proposals,
        "gate_reports": gate_reports,
        "promoted": 0,  # ALWAYS 0 — promotion is manager-gated (DR12)
        "verdict": "pipeline_works" if signals else "no_gaps_detected",
    }
