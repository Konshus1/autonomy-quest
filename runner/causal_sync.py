"""Best-effort bridge: after a productive cycle, refresh the mined causal principles (BB #764).

The loop's job is to TURN THE MISSION, not to own the causal store's persistence. So this is a
fire-and-forget nudge to the already-reviewed ``POST /api/causal/mine`` path (which does the
dedup-by-run, preserve-earned-support, and provenance work) — deliberately NOT a second copy of
that accumulation logic, so the two can never diverge.

Every failure is swallowed: a completed, durably-recorded cycle must never be undone by a
principle-refresh hiccup (management API down, a unit test with no server, a network blip).
Stdlib-only (urllib) — the loop takes on no new dependency.

Efficiency note: the mine endpoint re-mines from full history each call. That is idempotent and
non-destructive (re-mining preserves earned support), and fine at demo scale; incremental
single-cycle mining is a roadmap follow-up.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger("aq.causal")

_DISABLED = {"0", "false", "no", "off"}


def mgmt_base_url(env: dict[str, str] | None = None) -> str | None:
    """Resolve the management API base URL for the causal refresh, or None to skip.

    Returns None (skip silently) when auto-mining is disabled or no management surface is
    configured — e.g. the unit-test path that drives ``Loop.cycle`` with no API running.
    """
    e = env if env is not None else os.environ
    if str(e.get("AQ_CAUSAL_AUTOMINE", "1")).lower() in _DISABLED:
        return None
    url = e.get("AQ_MGMT_URL")
    if url:
        return url.rstrip("/")
    port = e.get("AQ_MGMT_PORT")
    if port:
        return f"http://127.0.0.1:{port}"
    return None


def _post_json(base_url: str, path: str, payload: dict, timeout: float) -> dict | None:
    """POST json to base_url+path; return the parsed body IF it is a dict, else None.

    Best-effort: ANY failure returns None. The Request construction is INSIDE the try because a
    scheme-less/garbage base_url raises 'unknown url type' at construction, not at urlopen. A body
    that decodes but is not a JSON object also returns None, so callers never see a non-dict.
    """
    try:
        req = urllib.request.Request(
            f"{base_url}{path}",
            method="POST",
            headers={"content-type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body if isinstance(body, dict) else None
    except Exception as exc:  # never propagate into the loop
        log.debug("causal call %s skipped: %s", path, exc)
        return None


def assess_plan_certainty(base_url: str, cause: str, effect: str, timeout: float = 2.0) -> float | None:
    """CONSULT: what certainty do the mined principles give this (cause -> effect) step?

    Returns the governing edge's certainty, or None when no principle governs it yet (uncovered)
    or the API is unreachable. Read-only: this scores the plan, it does not choose the work.
    """
    prof = _post_json(base_url, "/api/causal/assess-plan",
                      {"steps": [{"action": cause, "effect": effect}]}, timeout)
    try:
        steps = (prof or {}).get("per_step") or []
        step = steps[0] if steps else {}
        if not isinstance(step, dict) or not step.get("covered"):
            return None
        return float(step["certainty"])
    except (KeyError, ValueError, TypeError, IndexError, AttributeError) as exc:
        # a contract-drifted response body must never raise into the PRE-ACT consult and wedge
        # the loop — a mis-shaped profile is simply "no usable prediction".
        log.debug("assess-plan shape unusable, treating as uncovered: %s", exc)
        return None


def record_outcome_surprise(base_url: str, cause: str, effect: str,
                            predicted_certainty: float, actual_success: bool,
                            timeout: float = 2.0) -> dict | None:
    """LEARN: record the act's outcome as surprise on the governing edge (earns/demotes support).

    Returns {surprise, proposal} or None (no governing edge / unreachable). Best-effort — the
    cycle is already recorded; a scoring miss must never affect it.
    """
    return _post_json(base_url, "/api/causal/record-outcome",
                      {"cause": cause, "effect": effect, "scope": {},
                       "predicted_certainty": predicted_certainty,
                       "actual_success": bool(actual_success)}, timeout)


def refresh_causal_principles(base_url: str, timeout: float = 2.0) -> int | None:
    """POST /api/causal/mine; return the mined-edge count, or None on ANY failure (best-effort).

    A 503 (no Postgres-backed causal store / mission tables absent) is treated as a clean skip:
    there is simply nothing to mine, not an error to surface.
    """
    body = _post_json(base_url, "/api/causal/mine", {}, timeout)
    if not body:
        return None
    try:
        mined = int(body.get("mined", 0))
    except (TypeError, ValueError):  # {"mined": null} / non-numeric -> no usable count
        return None

    # T10: after mining, scan the now-larger corpus for conceptual inconsistencies.
    # This is the C4b trigger — the "Maxwell-vs-Newton detector" — running live.
    # It is READ-ONLY: flags, never mutates. Any conflicts enter the held-investigation path
    # via ralph_surprise_packet_v0 with surprise_type="conceptual_inconsistency".
    # Best-effort: a scan failure must never affect the mining result already recorded.
    if mined is not None and mined > 0:
        try:
            scan = _post_json(base_url, "/api/causal/scan-inconsistencies", {}, timeout + 3.0)
            if scan and scan.get("ok"):
                report = scan.get("report", {})
                conflicts = report.get("conflicts_found", 0)
                if conflicts > 0:
                    log.warning("T10 scan found %d conceptual inconsistencies after mining %d edges",
                                conflicts, mined)
        except Exception as exc:
            log.debug("T10 inconsistency scan skipped: %s", exc)

    return mined


def feed_frame_expansion(
    base_url: str,
    work_kind: str,
    work_summary: str,
    learning_insight: str,
    outcome: str,
    succeeded: bool,
    timeout: float = 3.0,
) -> dict | None:
    """T11: feed a cycle's learning as an episode into the frame-expansion pipeline.

    After each cycle's learning is written, convert the learning into an episode
    (attributes extracted from the work kind + insight) and run T11's frame-expansion
    to detect if the system's current dimension library can't describe something
    it just learned. If mapping_exhausted fires, the system has encountered a concept
    it has no category for — the C10 signal.

    This is best-effort: a frame-expansion failure must never affect the cycle.
    """
    # Extract attributes from the learning — these are the "concepts" the system
    # just learned about. We use the work kind + key nouns from the insight.
    attributes = _extract_attributes(work_kind, learning_insight)
    if not attributes:
        return None

    episode = {
        "episode_id": f"cycle_{work_kind}",
        "attributes": attributes,
        "relational_graph": {
            "nodes": [{"id": work_kind, "type": "action"}],
            "edges": [{"src": work_kind, "dst": "outcome", "relation": "produces"}],
        },
    }

    result = _post_json(
        base_url,
        "/api/causal/frame-expansion",
        {"episodes": [episode], "mode": "situation_driven"},
        timeout,
    )

    if result and result.get("ok"):
        fr = result.get("result", {})
        signals = fr.get("mapping_exhausted_signals", [])
        if signals:
            log.info("T11: mapping_exhausted on episode %s — %d uncapped attributes: %s",
                     episode["episode_id"],
                     len(signals[0].get("uncapped_attributes", [])),
                     [a["attribute"] for a in signals[0].get("uncapped_attributes", [])])

    return result


def _extract_attributes(work_kind: str, insight: str) -> list[str]:
    """Extract concept attributes from a learning for frame-expansion mapping.

    We look for nouns/key phrases in the insight that represent concepts the
    system just learned about. These are what T11 tries to map to dimensions.
    """
    # Start with the work kind — it's the action category
    attrs = [work_kind] if work_kind else []

    # Extract key nouns from the insight. Simple heuristic: words longer than
    # 4 chars that aren't stop words. In production this would use NLP/embeddings.
    stop = {"the", "this", "that", "what", "when", "which", "there", "their", "they",
            "have", "has", "been", "were", "more", "most", "than", "then", "also",
            "from", "with", "will", "would", "could", "should", "about", "into",
            "only", "each", "very", "just", "your", "were", "being", "must", "some"}
    words = insight.lower().replace(".", " ").replace(",", " ").replace(":", " ").split()
    seen = set(attrs)
    for w in words:
        w = w.strip()
        if len(w) > 4 and w not in stop and w not in seen and w.isalpha():
            attrs.append(w)
            seen.add(w)
        if len(attrs) >= 8:  # cap to avoid noise
            break

    return attrs
