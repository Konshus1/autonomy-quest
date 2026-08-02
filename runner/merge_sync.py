"""Stage 8 — manager-gated merge decision as a live loop step (EVAL_MERGE_SPEC.md, first slice).

Mirrors runner/causal_sync.py's best-effort contract: builds a manager-merge packet from the
evaluator's verdict and POSTs it to /api/manager/merge-decision (persists to ralph_merges, visible
in /api/ralph/state counts.merge_decisions). POST-COMMIT and BEST-EFFORT — the run is already
durably recorded, so a merge-API hiccup must degrade to "no decision recorded", never fail the
cycle. Stdlib-only.

First-slice scope (honest): cohort-of-one (cohort_id = f"cohort-run-{run_id}"); the "merge" is a
DECISION RECORD, not a git merge. run linkage is carried in the rationale text because the API's
ManagerMergeIn forbids extra fields. `rework` verdicts emit nothing — unmerged work is simply the
loop continuing to climb the escalation ladder.
"""

from __future__ import annotations

import json
import logging
import urllib.request

from ralph_portable.manager_merge_gate import validate_manager_merge_decision

log = logging.getLogger("aq.merge")


def build_merge_packet(*, ev, run_id: int, work, manager_handle: str, cohort_id: str) -> dict:
    """Build a ManagerMergeIn-shaped packet from the evaluator verdict. Only the exact API fields
    (decision/manager_handle/cohort_id/rationale/uncertainty_note) — no extras (the endpoint forbids
    them). Run linkage lives in the rationale."""
    base = {"manager_handle": manager_handle, "cohort_id": cohort_id}
    if ev.verdict == "pass":
        return {
            **base,
            "decision": "approve_merge",
            "rationale": (f"run #{run_id} / {cohort_id}: {ev.test_level} tests pass; "
                          f"intent covered; completeness + coherence clean."),
        }
    # escalate — uncertainty_note is REQUIRED by the validator when escalating
    return {
        **base,
        "decision": "escalate_human",
        "rationale": f"run #{run_id} / {cohort_id}: manager cannot clear on cursory review.",
        "uncertainty_note": ev.detail,
    }


def submit_merge_decision(base_url: str, packet: dict, timeout: float = 2.0) -> dict | None:
    """Validate locally (never POST a known-invalid packet), then POST best-effort. Returns the
    response body if a dict, else None; ANY failure returns None and never raises into the loop."""
    if validate_manager_merge_decision(packet):  # non-empty error list -> invalid, don't send
        log.debug("merge packet failed local validation; not sending: %s", packet.get("decision"))
        return None
    try:
        req = urllib.request.Request(
            f"{base_url}/api/manager/merge-decision",
            method="POST",
            headers={"content-type": "application/json"},
            data=json.dumps(packet).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body if isinstance(body, dict) else None
    except Exception as exc:  # never propagate into the loop
        log.debug("merge-decision submission skipped: %s", exc)
        return None
