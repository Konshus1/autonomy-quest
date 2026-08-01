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


def refresh_causal_principles(base_url: str, timeout: float = 2.0) -> int | None:
    """POST /api/causal/mine; return the mined-edge count, or None on ANY failure (best-effort).

    A 503 (no Postgres-backed causal store / mission tables absent) is treated as a clean skip:
    there is simply nothing to mine, not an error to surface.
    """
    req = urllib.request.Request(
        f"{base_url}/api/causal/mine",
        method="POST",
        headers={"content-type": "application/json"},
        data=b"{}",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return int(body.get("mined", 0))
    except Exception as exc:  # never propagate into the loop
        log.debug("causal principle refresh skipped: %s", exc)
        return None
