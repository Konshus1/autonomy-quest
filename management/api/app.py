"""FastAPI management API — Task #4407 Docker OOTB path.

Exposes the abstract surfaces so the Docker kit and prompt kit share one contract.
Wired into container/Dockerfile (COPY management/) and started by container/entrypoint.sh
(supervise_management on AQ_MGMT_PORT, default 8090) beside the loop — a management-API
crash never stops the loop. Serves an interim vanilla-JS console at `/`; the React/Vite
build is a follow-up slice (see DOCKER_OOTB_PLAN.md).

State backing is pluggable (v6): durable Postgres when a DB URL is configured and
reachable, else in-memory. Response field shapes are identical either way; only
``state.backing`` changes observably ("in_memory_stub" -> "postgres").
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("aq.management")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from management.api.causal_store import build_causal_store
from management.api.store import StoreUnavailable, build_store
from ralph_portable.causal_edges import edge_identity as causal_edge_identity
from ralph_portable.causal_edges import surprise as causal_surprise
from ralph_portable.manager_merge_gate import validate_manager_merge_decision
from ralph_portable.replication_request import (
    OVERRIDE_ENV,
    initial_status_for_host,
    override_enabled,
    validate_replication_request,
)

app = FastAPI(title="AQ Ralph Control Management", version="0.2.0")

# Interim honest console (vanilla JS, no build step). The React/Vite build is a
# follow-up slice documented in DOCKER_OOTB_PLAN.md.
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_INDEX_HTML = _WEB_DIR / "index.html"
# The built React/Vite shell (management/frontend -> npm run build -> web/dist). Preferred at `/`
# when present; the interim vanilla console stays as the no-build fallback for a partial image.
_DIST_DIR = _WEB_DIR / "dist"
_DIST_INDEX = _DIST_DIR / "index.html"

# Durable state backing (v6): Postgres when a DB URL is configured and reachable,
# else in-memory. Response *shapes* are identical either way (contract locked with
# the React shell, v7) — only state.backing changes observably.
store = build_store()

# Causal front-half (BB #746, roadmap surfaced live): the durable, identity-keyed causal-edge
# store + the read-only fuzzy planning check + a surprise loop that earns support and PROPOSES a
# gated promote/demote (the proposal is never auto-applied — no edge is promoted in the live
# system today; formalization is roadmap). Postgres when configured, else in-memory — same shape.
causal = build_causal_store()


@app.exception_handler(StoreUnavailable)
def _store_unavailable_handler(request: Request, exc: StoreUnavailable) -> JSONResponse:
    """A mid-session DB outage degrades to a stable 503 — not a 500 traceback and not a
    silently-empty 200 (which would be data confusion). The client can retry."""
    return JSONResponse(
        status_code=503,
        content={
            "ok": False,
            "error": "state store temporarily unavailable",
            "backing": getattr(store, "backing", "unknown"),
        },
    )


class ReplicationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # reject unknown fields (no laundering)
    mode: str
    mission_id: str
    requester_instance_id: str
    modifications: dict[str, Any] | None = None


class ManagerMergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str
    manager_handle: str
    cohort_id: str
    rationale: str
    uncertainty_note: str | None = None


# Serve the built React shell's hashed assets when the build exists. Mounting is additive:
# if web/dist/assets is absent (no build), the mount is simply not created and `/` falls back
# to the interim console — a partial image still boots an honest surface.
if (_DIST_DIR / "assets").is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=_DIST_DIR / "assets"), name="assets")


@app.get("/", response_class=HTMLResponse)
def console() -> Any:
    """Serve the management console. Prefers the built React/Vite shell (web/dist), falls back
    to the interim vanilla console, then to a plain notice — so `/` never 500s on a partial image."""
    if _DIST_INDEX.is_file():
        return FileResponse(_DIST_INDEX)
    if _INDEX_HTML.is_file():
        return FileResponse(_INDEX_HTML)
    return HTMLResponse(
        "<h1>AQ Ralph Control Management</h1>"
        "<p>Console asset missing; API is live. See <a href='/docs'>/docs</a>"
        " and <a href='/health'>/health</a>.</p>"
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "kit": "ralph_control_management",
        "replication_override_env": OVERRIDE_ENV,
        "replication_override_enabled": override_enabled(),
        "merge_gate": "manager_gated",
    }


@app.get("/api/ralph/state")
def ralph_state() -> dict[str, Any]:
    """Snapshot of the control state (v6: durable-backed when a DB is configured).

    ``backing`` reflects the live store ("in_memory_stub" or "postgres"). Field
    shapes are stable across both — do not branch on the literal (contract with the
    React shell, v7). See BB #2141/#2144.
    """
    replications = store.replications()
    return {
        "ok": True,
        "backing": store.backing,
        "merge_gate": "manager_gated",
        "replication": {
            "override_env": OVERRIDE_ENV,
            "override_enabled": override_enabled(),
            "pending": sum(1 for r in replications if r.get("status") != "executed"),
            "total": len(replications),
        },
        "counts": {
            "workstreams": len(store.workstreams()),
            "tasks": len(store.tasks()),
            "agent_comms": len(store.comms()),
            "merge_decisions": len(store.merges()),
        },
    }


@app.get("/api/workstreams")
def workstreams() -> dict[str, Any]:
    return {"items": store.workstreams()}


@app.get("/api/tasks")
def tasks() -> dict[str, Any]:
    return {"items": store.tasks()}


@app.get("/api/agent-comms")
def agent_comms() -> dict[str, Any]:
    return {"items": store.comms()}


class CommIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_handle: str = Field(min_length=1)
    text: str = Field(min_length=1)
    to_handle: str | None = None
    kind: str = "message"


@app.post("/api/agent-comms")
def create_comm(body: CommIn) -> dict[str, Any]:
    """Record an agent-comms message (v8: local durable table, additive surface).

    Item shape: {id, from_handle, to_handle, text, kind, ts}. GET /api/agent-comms
    still returns {"items": [...]} — now non-empty. Not the tbagents bus; this is the
    kit's own in-container comms log so the React shell has real rows to render.
    """
    item = store.create_comm(body.from_handle, body.to_handle, body.text, body.kind)
    return {"ok": True, "item": item}


@app.post("/api/replication/propose")
def propose_replication(body: ReplicationIn) -> dict[str, Any]:
    packet = body.model_dump()
    if body.modifications is None:
        packet.pop("modifications", None)
    errors = validate_replication_request(packet)
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    status = initial_status_for_host()
    record = {**packet, "status": status, "executed_by": None}
    store.add_replication(record)
    return {
        "ok": True,
        "status": status,
        "note": "Guest proposed; host must execute. OVERRIDE="
        f"{OVERRIDE_ENV}=>{override_enabled()}",
        "record": record,
    }


@app.post("/api/manager/merge-decision")
def manager_merge(body: ManagerMergeIn) -> dict[str, Any]:
    packet = {k: v for k, v in body.model_dump().items() if v is not None}
    errors = validate_manager_merge_decision(packet)
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    store.add_merge(packet)
    return {"ok": True, "decision": packet["decision"], "record": packet}


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    workstream_id: str = "ws-default"


@app.post("/api/tasks")
def create_task(body: TaskIn) -> dict[str, Any]:
    item = store.create_task(body.title, body.workstream_id)
    return {"ok": True, "item": item}


# ---- Causal front-half surfaces (BB #746 — roadmap, surfaced live) ----

class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[dict[str, Any]]
    bounded_experiment: bool = False


class OutcomeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: str
    effect: str
    scope: dict[str, Any] | None = None
    predicted_certainty: float
    actual_success: bool
    # When supplied by the mission loop, these fields put automatic governance on the SAME
    # live outcome path rather than leaving it behind a demonstration-only endpoint.
    environment: dict[str, Any] | None = None
    evidence_ref: str | None = None
    observed_delta: float | None = None
    expected_direction: str = "increase"
    noise_tolerance: float = Field(default=0.0, ge=0.0)


class GovernanceTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: str
    effect: str
    scope: dict[str, Any] | None = None
    environment: dict[str, Any]
    evidence_ref: str = Field(min_length=1)
    expected_direction: str
    observed_delta: float
    noise_tolerance: float = Field(default=0.0, ge=0.0)
    recorded_by: str = "aq-evaluator"


class GovernancePromotionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: str
    effect: str
    scope: dict[str, Any] | None = None
    authorization_environment: dict[str, Any]
    evidence_ref: str = Field(min_length=1)
    applies_here: bool
    applies_here_how: str = Field(min_length=1)
    negative_control: str = Field(min_length=1)
    negative_control_result: str = Field(min_length=1)
    adjudicated_by: str = Field(min_length=1)


@app.get("/api/causal/edges")
def causal_edges() -> dict[str, Any]:
    return {"items": causal.all()}


# System-managed edge fields: EARNED by the surprise loop or DERIVED by the miner, never
# caller-set. Stripped from external PUTs so a client can't launder a forced promotion
# (e.g. support_count=999) or fake provenance/mined status into the jsonb. The internal
# miner reaches causal.put() directly and is unaffected.
_SYSTEM_MANAGED_EDGE_FIELDS = frozenset(
    {"support_count", "evidence", "provenance", "evidence_run_ids", "observed_runs", "mined",
     # Formal layer (FORMAL_LAYER_SPEC §2/§7): set ONLY by attach/verify, never a caller —
     # so a client can't forge a verified/proven executor to launder a formal promotion.
     "executor_verified", "executor_provenance", "oracle_proof"}
)


@app.post("/api/causal/edges")
def put_causal_edge(edge: dict[str, Any]) -> dict[str, Any]:
    """Upsert a causal edge (identity = cause/effect/scope; validated server-side).

    Caller-supplied system-managed fields (earned support, provenance, mined flag) are dropped:
    they are set only by the surprise loop and the miner, never by an external write.
    """
    clean = {k: v for k, v in edge.items() if k not in _SYSTEM_MANAGED_EDGE_FIELDS}
    # Laundering guard (FORMAL_LAYER_SPEC §7, BB #775): formality=formal is reachable ONLY through
    # attach-executor -> verify (a passing sandboxed oracle proof) -> promote. A raw external PUT can
    # never mint a formal (is_guaranteed / plan-certainty-1.0) edge; it may only re-state an edge
    # that ALREADY reached formal legitimately (existing executor_verified).
    if clean.get("formality") == "formal":
        existing = causal.get(causal_edge_identity(clean))
        if not (existing and existing.get("executor_verified")):
            raise HTTPException(
                status_code=400,
                detail="formality=formal cannot be set by PUT — reach it via attach-executor + "
                       "verify (oracle proof) + promote (FORMAL_LAYER_SPEC §7 / BB #775)")
    try:
        ident = causal.put(clean)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "identity": list(ident)}


@app.post("/api/causal/mine")
def mine_principles() -> dict[str, Any]:
    """Mine candidate fuzzy causal edges from the mission loop's own run->learning outcomes."""
    miner = getattr(causal, "mine_from_mission_loop", None)
    if miner is None:
        raise HTTPException(
            status_code=503,
            detail="principle mining requires a Postgres-backed causal store (no DB configured)",
        )
    return miner()


@app.post("/api/causal/assess-plan")
def assess_plan(body: PlanIn) -> dict[str, Any]:
    """The planning check: only promoted rules carry authority.

    Provisional/demoted rules are invisible to ordinary planning.  They may be consulted only
    when the caller explicitly declares a bounded experiment, and are labelled non-authoritative.
    """
    if getattr(causal, "governance", None) is not None:
        return causal.assess_plan(body.steps, bounded_experiment=body.bounded_experiment)
    return causal.assess_plan(body.steps)


def _require_evidence_authorization(request: Request) -> None:
    import secrets
    configured = os.environ.get("AQ_GOVERNANCE_EVIDENCE_TOKEN", "")
    supplied = request.headers.get("x-aq-governance-evidence-token", "")
    if not configured:
        raise HTTPException(status_code=503, detail="AQ_GOVERNANCE_EVIDENCE_TOKEN is not configured")
    if not secrets.compare_digest(configured, supplied):
        raise HTTPException(status_code=403, detail="trusted governance evidence required")


@app.post("/api/causal/record-outcome")
def record_outcome(body: OutcomeIn, request: Request) -> dict[str, Any]:
    """Record an act outcome as surprise on the governing edge; returns a GATED update proposal."""
    ident = causal_edge_identity({"cause": body.cause, "effect": body.effect, "scope": body.scope or {}})
    s = causal_surprise(body.predicted_certainty, body.actual_success)
    governed = None
    gov = getattr(causal, "governance", None)
    if gov is not None and body.environment is not None and body.evidence_ref and body.observed_delta is not None:
        _require_evidence_authorization(request)
        from management.api.principle_governance import GovernanceError
        try:
            # Safety ordering is deliberate: withdraw authority before the legacy JSON evidence
            # append. A crash between the two may delay support accounting, but cannot leave a
            # refuted rule authoritative.
            governed = gov.record_environment_test(
                ident, body.environment, body.evidence_ref, body.expected_direction,
                body.observed_delta, body.noise_tolerance, "aq-live-outcome")
        except GovernanceError as exc:
            raise HTTPException(status_code=409, detail=f"governance outcome rejected: {exc}")
    try:
        proposal = causal.record_evidence(ident, s)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no causal edge for identity {list(ident)}")
    return {"ok": True, "surprise": s, "proposal": proposal, "governance": governed}


def _governance():
    gov = getattr(causal, "governance", None)
    if gov is None:
        raise HTTPException(status_code=503, detail="principle governance requires PostgreSQL backing")
    return gov


@app.get("/api/causal/governance/history")
def governance_history(cause: str, effect: str, scope: str = "{}") -> dict[str, Any]:
    try:
        parsed_scope = __import__("json").loads(scope)
        return {"items": _governance().history({"cause": cause, "effect": effect,
                                                  "scope": parsed_scope})}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/causal/governance/test")
def governance_test(body: GovernanceTestIn, request: Request) -> dict[str, Any]:
    """Record trusted validation; new-domain refutation demotes inline."""
    _require_evidence_authorization(request)
    from management.api.principle_governance import GovernanceError
    edge = {"cause": body.cause, "effect": body.effect, "scope": body.scope or {}}
    try:
        return {"ok": True, **_governance().record_environment_test(
            edge, body.environment, body.evidence_ref, body.expected_direction,
            body.observed_delta, body.noise_tolerance, body.recorded_by)}
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/causal/governance/promote")
def governance_promote(body: GovernancePromotionIn, request: Request) -> dict[str, Any]:
    """Separately authorize AQ's own rule; two cross-domain supports are mandatory.

    Unlike the historical manager_handle-shaped routes, the promotion actor cannot merely name
    itself.  Deployment must configure AQ_GOVERNANCE_TOKEN and the caller must possess it.
    """
    import secrets
    configured = os.environ.get("AQ_GOVERNANCE_TOKEN", "")
    supplied = request.headers.get("x-aq-governance-token", "")
    if not configured:
        raise HTTPException(status_code=503, detail="AQ_GOVERNANCE_TOKEN is not configured")
    if not secrets.compare_digest(configured, supplied):
        raise HTTPException(status_code=403, detail="governance authorization required")
    configured_adjudicator = os.environ.get("AQ_GOVERNANCE_ADJUDICATOR", "").strip()
    if not configured_adjudicator:
        raise HTTPException(status_code=503, detail="AQ_GOVERNANCE_ADJUDICATOR is not configured")
    if body.adjudicated_by != configured_adjudicator:
        raise HTTPException(status_code=403, detail="adjudicated_by must match authenticated adjudicator")
    from management.api.principle_governance import GovernanceError
    edge = {"cause": body.cause, "effect": body.effect, "scope": body.scope or {}}
    try:
        tid = _governance().promote(
            edge, authorization_environment=body.authorization_environment,
            evidence_ref=body.evidence_ref, applies_here=body.applies_here,
            applies_here_how=body.applies_here_how, negative_control=body.negative_control,
            negative_control_result=body.negative_control_result,
            adjudicated_by=body.adjudicated_by)
        return {"ok": True, "status": "promoted", "transition_id": tid}
    except GovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ---- T10: Conceptual-inconsistency detector (C4b/DR4) ----
# The "Maxwell-vs-Newton detector" — scans the principle corpus for internal
# contradictions WITHOUT waiting for an outcome failure. Emits
# ralph_surprise_packet_v0 with surprise_type="conceptual_inconsistency".
# READ-ONLY: flags, never mutates. Origin: BB #2358 jump-system gap analysis.

@app.post("/api/causal/scan-inconsistencies")
def scan_inconsistencies() -> dict[str, Any]:
    """T10: scan all active causal edges for conceptual inconsistencies.

    Uses the LLM classifier when available (Option C: AQ executor, Option B:
    OpenRouter fallback). Falls back to heuristic when no LLM is configured.

    Surprise packets are persisted as evidence on the governing causal edges.
    READ-ONLY on principles: never demotes, merges, or edits.
    """
    from ralph_portable.inconsistency_detector import (
        scan_inconsistencies as _scan,
        make_executor_classify_fn,
        make_openrouter_classify_fn,
    )
    from ralph_portable.causal_edges import surprise as causal_surprise

    # Build the LLM classify function in priority order (Kevin BB #856):
    # C: AQ executor -> B: OpenRouter direct -> None (heuristic fallback)
    llm_classify_fn = None
    classifier_source = "heuristic"

    # Option C: check if an executor is available via the loop's module global
    # (same-process access — the standard AQ deployment where loop + API share a process)
    if llm_classify_fn is None and os.environ.get("AQ_EXECUTOR_AVAILABLE") == "1":
        try:
            import runner.loop as _loop_mod
            if _loop_mod._active_executor is not None:
                llm_classify_fn = make_executor_classify_fn(_loop_mod._active_executor)
                classifier_source = "aq_executor"
        except ImportError:
            pass  # running outside the loop process — try OpenRouter
        except Exception as exc:
            log.warning("executor classifier init failed: %s — trying OpenRouter", exc)

    # Option B: OpenRouter fallback (key from env only, never from repo)
    if llm_classify_fn is None:
        or_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OR_API_KEY")
        if or_key:
            or_model = os.environ.get("T10_CLASSIFIER_MODEL", "moonshotai/kimi-k3")
            try:
                llm_classify_fn = make_openrouter_classify_fn(or_key, or_model)
                classifier_source = "openrouter"
            except Exception as exc:
                log.warning("OpenRouter classifier init failed: %s — using heuristic", exc)

    # If neither C nor B is available, llm_classify_fn stays None -> heuristic
    edges = causal.all()
    principles = [_edge_to_principle(e) for e in edges]
    report = _scan(principles, llm_classify_fn=llm_classify_fn)
    report["classifier"] = classifier_source

    # Persist surprise packets as evidence on the governing edges (O1 fix).
    persisted = 0
    for packet in report.get("surprise_packets", []):
        refs = packet.get("evidence_refs", [])
        if len(refs) >= 2:
            pid_a = refs[0]
            if "->" in pid_a:
                cause, effect = pid_a.split("->", 1)
                ident = (cause, effect, "{}")
                try:
                    s = causal_surprise(
                        predicted_certainty=0.5,
                        actual_success=False,
                    )
                    s["surprise_type"] = "conceptual_inconsistency"
                    s["classification"] = packet.get("classification", "")
                    s["scope"] = packet.get("scope", "")
                    s["explanation"] = packet.get("explanation", "")
                    s["conflicting_principle"] = refs[1] if len(refs) > 1 else ""
                    s["classifier"] = classifier_source
                    causal.record_evidence(ident, s)
                    persisted += 1
                except (KeyError, Exception) as exc:
                    log.warning("T10: failed to persist surprise packet for %s: %s", pid_a, exc)

    if report["conflicts_found"] > 0:
        log.warning("T10 scan found %d conceptual inconsistencies (%d persisted, classifier=%s)",
                     report["conflicts_found"], persisted, classifier_source)

    return {"ok": True, "report": report, "persisted": persisted, "classifier": classifier_source}


def _edge_to_principle(edge: dict[str, Any]) -> dict[str, Any]:
    """Convert a causal edge to the principle format the T10 scanner expects.

    Production-mined edges (from principle_mining.py) have NO tags, failure_modes,
    or formalization_hint — only cause, effect, scope, certainty, formality.
    We synthesize tags from the cause/effect pair so Phase 1 has overlap signal.
    """
    cause = edge.get("cause", "")
    effect = edge.get("effect", "")
    # Synthesize tags from cause/effect so Phase 1 can find overlapping pairs
    tags = edge.get("tags")
    if not tags:
        tags = [cause, effect] if cause and effect else []
    return {
        "principle_id": f"{cause}->{effect}" if cause and effect else str(edge.get("id", "?")),
        "principle_text": f"{cause} causes {effect} (certainty: {edge.get('certainty', 0)})",
        "tags": tags,
        "status": "active",
        "is_active": True,
        "failure_modes_addressed": edge.get("failure_modes_addressed", []),
        "formalization_hint": edge.get("formalization_hint", ""),
    }


# ---- T11: Frame-expansion mechanism (C10/DR5) ----
# The "no category for this" detector + dimension inductor. Notices when
# structural mapping fails on uncapped attributes and proposes new descriptive
# dimensions. Manager-gated promotion (no auto-promotion, DR12).
# Origin: BB #2358 jump-system gap analysis.

_frame_library = None  # lazily initialized


def _get_frame_library():
    global _frame_library
    if _frame_library is None:
        from ralph_portable.frame_expansion import DimensionLibrary, PgDimensionLibrary
        # Use Postgres-backed library when a DB URL is configured (S3 fix: survive restarts)
        dsn = os.environ.get("AQ_MGMT_DB_URL") or os.environ.get("AQ_DB_URL")
        if dsn:
            try:
                _frame_library = PgDimensionLibrary(dsn)
            except Exception as exc:
                log.warning("PgDimensionLibrary unavailable (%s); using in-memory", exc)
                _frame_library = DimensionLibrary()
        else:
            _frame_library = DimensionLibrary()
    return _frame_library


class FrameExpansionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episodes: list[dict[str, Any]]  # each: {episode_id, attributes: [str], relational_graph?: {}}
    mode: str = "situation_driven"


@app.post("/api/causal/frame-expansion")
def frame_expansion(body: FrameExpansionIn) -> dict[str, Any]:
    """T11: run the frame-expansion pipeline on a set of episodes.

    Detects mapping_exhausted signals, accumulates recurring mismatches,
    proposes new dimensions (status="proposed"), and gates promotion.
    No dimensions are auto-promoted — manager approval required (DR12).
    """
    from ralph_portable.frame_expansion import run_frame_expansion

    library = _get_frame_library()
    result = run_frame_expansion(
        episodes=body.episodes,
        library=library,
        mode=body.mode,
    )
    return {"ok": True, "result": result}


@app.get("/api/causal/dimensions")
def dimensions() -> dict[str, Any]:
    """List the active dimension library and any proposed candidates."""
    library = _get_frame_library()
    return {
        "active": library.all(),
        "candidates": library.candidates(),
    }


class DimensionPromoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    manager_handle: str = Field(min_length=1)


@app.post("/api/causal/dimensions/promote")
def promote_dimension(body: DimensionPromoteIn) -> dict[str, Any]:
    """Manager-gated promotion of a proposed dimension into the active library.

    Requires an explicit manager handle and rationale (DR12: no LLM-only gating).
    The dimension enters the active library, changing the retrieval landscape
    for future analogy-based planning.
    """
    library = _get_frame_library()
    result = library.promote(body.candidate_id, body.reason)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no candidate dimension with id '{body.candidate_id}'")
    return {"ok": True, "promoted": result}
