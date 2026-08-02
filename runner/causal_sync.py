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
        return int(body.get("mined", 0))
    except (TypeError, ValueError):  # {"mined": null} / non-numeric -> no usable count
        return None
