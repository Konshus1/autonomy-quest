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
import hashlib
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger("aq.causal")

_DISABLED = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class PlanAuthorizationDecision:
    """Strict pre-ACT decision. Only a validated remote receipt can allow or abstain."""

    disposition: Literal["allow", "block", "abstain", "defer"]
    reason_code: str
    reason: str
    may_act: bool
    selected: bool
    governed: bool
    global_plan_id: str
    request_digest: str
    authorization_id: str | None = None
    governor_transition_ids: tuple[int, ...] = ()
    policy_version: str | None = None


def _local_plan_digest(plan: dict) -> str:
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _defer(global_plan_id: str, plan: dict, reason_code: str, reason: str) -> PlanAuthorizationDecision:
    return PlanAuthorizationDecision(
        disposition="defer", reason_code=reason_code, reason=reason,
        may_act=False, selected=False, governed=False, global_plan_id=global_plan_id,
        request_digest=_local_plan_digest(plan),
    )


def governance_base_url(env: dict[str, str] | None = None) -> str | None:
    """Return only the explicitly configured narrow authority URL; never a management fallback."""
    source = env if env is not None else os.environ
    url = str(source.get("AQ_GOVERNANCE_URL") or "").strip()
    return url.rstrip("/") if url else None


def authorize_plan(global_plan_id: str, work_id: int, plan: dict, timeout: float = 3.0,
                   env: dict[str, str] | None = None) -> PlanAuthorizationDecision:
    """Request one durable checked decision; every transport/shape failure is a typed defer."""
    source = env if env is not None else os.environ
    base_url = governance_base_url(source)
    token = str(source.get("AQ_GOVERNANCE_DECISION_TOKEN") or "").strip()
    if not base_url or not token:
        return _defer(global_plan_id, plan, "governance_not_configured",
                      "narrow governance URL and decision token are required")
    payload = {"global_plan_id": global_plan_id, "work_id": int(work_id), "plan": plan}
    try:
        req = urllib.request.Request(
            f"{base_url}/api/causal/governance/authorize-plan", method="POST",
            headers={"content-type": "application/json",
                     "x-aq-governance-decision-token": token},
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        code = ("governance_unauthorized" if exc.code in (401, 403)
                else "governance_durability_failure" if exc.code == 409
                else "governance_http_error")
        return _defer(global_plan_id, plan, code, f"governance HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _defer(global_plan_id, plan, "governance_unreachable", type(exc).__name__)
    except Exception as exc:
        return _defer(global_plan_id, plan, "governance_malformed", type(exc).__name__)

    try:
        if not isinstance(body, dict):
            raise ValueError("response is not an object")
        disposition = body["disposition"]
        if disposition not in {"allow", "block", "abstain"}:
            raise ValueError("unknown disposition")
        expected = {
            "allow": (True, True, True),
            "block": (False, False, False),
            "abstain": (True, False, False),
        }[disposition]
        actual = (body.get("may_act"), body.get("selected"), body.get("governed"))
        if actual != expected:
            raise ValueError("disposition invariant mismatch")
        authorization_id = str(body["authorization_id"])
        request_digest = str(body["request_digest"])
        reason_code = str(body["reason_code"])
        reason = str(body["reason"])
        if (str(body.get("global_plan_id")) != global_plan_id or not authorization_id
                or len(request_digest) != 64 or not reason_code or not reason):
            raise ValueError("receipt identity or durability fields missing")
        governors = tuple(int(value) for value in body.get("governor_transition_ids", []))
        if disposition in {"allow", "block"} and not governors:
            raise ValueError("authoritative disposition lacks exact governor")
        return PlanAuthorizationDecision(
            disposition=disposition, reason_code=reason_code, reason=reason,
            may_act=expected[0], selected=expected[1], governed=expected[2],
            global_plan_id=global_plan_id, request_digest=request_digest,
            authorization_id=authorization_id, governor_transition_ids=governors,
            policy_version=str(body.get("policy_version") or ""),
        )
    except Exception as exc:
        return _defer(global_plan_id, plan, "governance_malformed", str(exc))

# Provenance stamp for the RETIRED T11 reflect-phase frame-expansion detector (BB #2430).
#
# The detector is left running deliberately (Kevin, option (b), 2026-08-09) rather than
# disabled, so the loop keeps continuity — but everything it emits must be self-labelling.
# This prefix rides on episode_id, which propose_dimension() carries into source_episodes,
# so it is PERSISTED on every candidate the mechanism produces. A future reader or lane
# encountering such a candidate sees its status without having to know the history.
#
# Grep this constant to find every artifact of the retired mechanism.
KNOWN_ARTIFACT_PREFIX = "KNOWN-ARTIFACT-BB2430-NOT-EVIDENCE__"


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


def _post_json(base_url: str, path: str, payload: dict, timeout: float,
               extra_headers: dict[str, str] | None = None) -> dict | None:
    """POST json to base_url+path; return the parsed body IF it is a dict, else None.

    Best-effort: ANY failure returns None. The Request construction is INSIDE the try because a
    scheme-less/garbage base_url raises 'unknown url type' at construction, not at urlopen. A body
    that decodes but is not a JSON object also returns None, so callers never see a non-dict.
    """
    try:
        req = urllib.request.Request(
            f"{base_url}{path}",
            method="POST",
            headers={"content-type": "application/json", **(extra_headers or {})},
            data=json.dumps(payload).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body if isinstance(body, dict) else None
    except Exception as exc:  # never propagate into the loop
        log.debug("causal call %s skipped: %s", path, exc)
        return None


def assess_plan_guidance(base_url: str, cause: str, effect: str,
                         timeout: float = 2.0) -> dict | None:
    """Read the exact governor receipt candidate; assessment itself never counts as selection."""
    prof = _post_json(base_url, "/api/causal/assess-plan",
                      {"steps": [{"action": cause, "effect": effect}]}, timeout)
    try:
        steps = (prof or {}).get("per_step") or []
        step = steps[0] if steps else {}
        if not isinstance(step, dict) or not step.get("covered"):
            return None
        float(step["certainty"])
        return step
    except (KeyError, ValueError, TypeError, IndexError, AttributeError) as exc:
        log.debug("assess-plan shape unusable, treating as uncovered: %s", exc)
        return None


def assess_plan_certainty(base_url: str, cause: str, effect: str, timeout: float = 2.0) -> float | None:
    step = assess_plan_guidance(base_url, cause, effect, timeout)
    return float(step["certainty"]) if step is not None else None


def record_plan_selection(base_url: str, governor: dict, *, plan_id: str, goal_id: str,
                          environment: dict, evidence_ref: str,
                          timeout: float = 2.0) -> int | None:
    """Append a trusted pre-ACT receipt for an unambiguous promoted governor."""
    identity = governor.get("identity") or []
    if len(identity) != 3 or not governor.get("promotion_transition_id"):
        return None
    try:
        scope = json.loads(identity[2])
    except (TypeError, ValueError):
        return None
    payload = {"cause": identity[0], "effect": identity[1], "scope": scope,
               "promotion_transition_id": governor["promotion_transition_id"],
               "plan_id": str(plan_id), "goal_id": str(goal_id),
               "environment": environment, "evidence_ref": evidence_ref}
    token = os.environ.get("AQ_GOVERNANCE_EVIDENCE_TOKEN")
    headers = {"x-aq-governance-evidence-token": token} if token else {}
    body = _post_json(base_url, "/api/causal/governance/select", payload, timeout, headers)
    try:
        return int((body or {}).get("usage_id"))
    except (TypeError, ValueError):
        return None


def record_outcome_surprise(base_url: str, cause: str, effect: str,
                            predicted_certainty: float, actual_success: bool,
                            timeout: float = 2.0, *, scope: dict | None = None,
                            environment: dict | None = None,
                            evidence_ref: str | None = None,
                            observed_delta: float | None = None,
                            expected_direction: str = "increase",
                            noise_tolerance: float = 0.0,
                            plan_id: str | None = None,
                            goal_reached: bool | None = None) -> dict | None:
    """LEARN: record the act's outcome as surprise on the governing edge (earns/demotes support).

    Returns {surprise, proposal} or None (no governing edge / unreachable). Best-effort — the
    cycle is already recorded; a scoring miss must never affect it.
    """
    payload = {"cause": cause, "effect": effect, "scope": scope or {},
               "predicted_certainty": predicted_certainty,
               "actual_success": bool(actual_success)}
    if environment is not None and evidence_ref and observed_delta is not None:
        payload.update({"environment": environment, "evidence_ref": evidence_ref,
                        "observed_delta": float(observed_delta),
                        "expected_direction": expected_direction,
                        "noise_tolerance": float(noise_tolerance)})
        if plan_id is not None and goal_reached is not None:
            payload.update({"plan_id": str(plan_id), "goal_reached": bool(goal_reached)})
    headers = {}
    evidence_token = os.environ.get("AQ_GOVERNANCE_EVIDENCE_TOKEN")
    if evidence_token and environment is not None:
        headers["x-aq-governance-evidence-token"] = evidence_token
    return _post_json(base_url, "/api/causal/record-outcome", payload, timeout, headers)


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

    *** DEPRECATED MECHANISM — ITS OUTPUT IS NOT EVIDENCE. See BB #2430. ***

    This asks "do I have a category for what I just learned?" — an ATTRIBUTE LOOKUP, run
    POST-COMMIT in reflect. Kevin's 2026-08-09 reformulation retired that question: the unit
    is a FRAME (a situation with relations, held against a goal), not a concept, and the real
    question is whether known relations compose a coherent path to the goal — evaluated in
    DECIDE, before acting. Sufficiency is meaningless once the act is over.

    Worse, this mechanism CANNOT NOT FIRE. Its matcher reaches only {0.0, 0.9, 1.0}, so
    MAPPING_EXHAUST_THRESHOLD is inert; the fire rule is disjunctive over ~8 positionally
    grabbed words; and a 112-token negative control drawn from the library's OWN definitions
    scored zero capped. P(fire) ~ 1 carries zero bits.

    IT IS LEFT RUNNING DELIBERATELY (Kevin, option (b), 2026-08-09) rather than disabled, so
    the loop keeps its continuity — but every episode it emits is STAMPED so its output is
    self-labelling. Disabling would rely on future readers remembering why a gap existed; the
    stamp does not rely on memory. This matters because these artifacts were cited as real C10
    evidence TWICE in 24 hours (#2421's "19 recurring mismatches", commit 62a9984's "171 frame
    gaps") by two different lanes before being retired.

    Replaced by: the goal-relative frame-sufficiency check in decide (task #5001, BB #2430),
    which also delivers decision #831's trigger 1c. DELETE THIS FUNCTION when that lands.

    This is best-effort: a frame-expansion failure must never affect the cycle.
    """
    # Extract attributes from the learning — these are the "concepts" the system
    # just learned about. We use the work kind + key nouns from the insight.
    attributes = _extract_attributes(work_kind, learning_insight)
    if not attributes:
        return None

    episode = {
        # PROVENANCE STAMP (BB #2430). episode_id flows into propose_dimension's
        # source_episodes and is therefore persisted on every candidate this mechanism
        # produces. Any proposal carrying this prefix came from the RETIRED, non-
        # discriminating detector and MUST NOT be cited as evidence of a frame gap.
        # Chosen over an API field because FrameExpansionIn forbids extras.
        "episode_id": f"{KNOWN_ARTIFACT_PREFIX}cycle_{work_kind}",
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
            # WARNING, not INFO, and self-labelling: this line previously read as a finding.
            # A reader scanning logs for C10 evidence must see the retirement in the same line
            # they see the number — a caveat elsewhere does not travel with a copied log line.
            log.warning(
                "T11 KNOWN-ARTIFACT (retired detector, superseded by BB #2430 — NOT EVIDENCE): "
                "mapping_exhausted on episode %s — %d uncapped attributes: %s",
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
