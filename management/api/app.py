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
from hmac import compare_digest
from pathlib import Path
from typing import Any

log = logging.getLogger("aq.management")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from management.api.causal_store import build_causal_store
from management.api.auth import device_login
from management.api.comms_auth import AuthError, CommsAuthenticator
from management.api.comms_envelope import (
    ALLOWED_KINDS,
    EnvelopeError,
    build_envelope,
    durability_required,
    legacy_projection,
)
from management.api.store import StoreUnavailable, build_store
from runner.approval import assert_valid_approval
from ui.server import state as flagship_state
from ralph_portable.causal_edges import edge_identity as causal_edge_identity
from ralph_portable.causal_edges import surprise as causal_surprise
from ralph_portable.manager_merge_gate import validate_manager_merge_decision
from ralph_portable.replication_modifications import (
    CONFIG_ALLOWLIST,
    validate_modification_packet,
)
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

# Agent-comms authenticator (#4834 comms Phase 0): verifies the per-instance / operator credential
# and DERIVES the sender principal from it. Built from env; tests replace this module global. It
# grants no capability — it only authenticates and enforces the publish ACL.
comms_auth = CommsAuthenticator.from_env()

# Causal front-half (BB #746, roadmap surfaced live): the durable, identity-keyed causal-edge
# store + the read-only fuzzy planning check + a surprise loop that earns support and PROPOSES a
# gated promote/demote (the proposal is never auto-applied — no edge is promoted in the live
# system today; formalization is roadmap). Postgres when configured, else in-memory — same shape.
causal = build_causal_store()


@app.get("/api/auth/status")
def auth_status() -> dict[str, object]:
    """Return non-secret Codex subscription status."""
    return device_login.status()


@app.post("/api/auth/device/start")
def auth_device_start(request: Request) -> dict[str, object]:
    """Start native Codex device authorization; tokens never cross this API."""
    # Browser requests must originate from this loopback UI. This blocks a malicious web page
    # from silently POSTing to localhost and disrupting the operator's login flow. CLI/curl calls
    # without an Origin remain available for local diagnostics.
    origin = request.headers.get("origin")
    allowed = {"http://localhost:8090", "http://127.0.0.1:8090"}
    if origin is not None and origin not in allowed:
        raise HTTPException(status_code=403, detail="device login must start from the local UI")
    return device_login.start()


@app.on_event("shutdown")
def stop_device_login() -> None:
    device_login.stop()


@app.on_event("startup")
def consume_evaluator_outcome_backlog() -> None:
    """Evaluator service startup recovers durable outcome events missed during an outage."""
    if not os.environ.get("AQ_EVALUATOR_TOKEN") or not os.environ.get("AQ_EVALUATOR_TRIGGER_TOKEN"):
        return
    try:
        gov = getattr(causal, "governance", None)
        if gov is not None and gov.checked_withdrawal_available():
            recovered = 0
            for _ in range(10):
                result = gov.consume_pending_plan_outcomes(100)
                recovered += int(result.get("consumed") or 0)
                if int(result.get("consumed") or 0) < 100:
                    break
            if recovered:
                log.info("evaluator recovered %s pending governed outcomes", recovered)
    except Exception:
        # The outbox is immutable and remains pending. Startup availability must not be coupled to
        # one transient DB error; the next trigger or restart retries deterministically.
        log.exception("evaluator outcome backlog recovery deferred")


@app.exception_handler(AuthError)
def _auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    """Comms authN/authZ failures map to stable 401 (unauthenticated) / 403 (ACL denied)."""
    return JSONResponse(status_code=exc.status, content={"ok": False, "error": exc.detail})


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


# DEPLOY-HYGIENE (#4834): the deployed loop's version + liveness, made observable.
# Threshold (seconds) under which a fresh cycle heartbeat counts as "cycling".
#
# It MUST comfortably exceed one loop interval PLUS a cycle's execution time. The heartbeat is
# written at cycle END (loop.py), then the loop sleeps a full interval (default 300s) before the
# next cycle even starts, which then runs for T seconds before the next beat. So on a perfectly
# healthy loop, seconds_since_last_cycle climbs to ~interval + T every period. A threshold AT the
# interval (the original 300s bug) therefore reads cycling:false during the entire execution window
# of every cycle — the exact false-alarm this work exists to eliminate. Default 900s ≈ 2×interval +
# margin (only the rate-limited path re-beats mid-wait; the ordinary inter-cycle sleep does not).
# tests/test_deploy_hygiene.py pins default >= 2× the loop's default interval so this can't regress.
# Env-overridable; no new required config.
_CYCLING_THRESHOLD_S = int(os.environ.get("AQ_LOOP_CYCLING_THRESHOLD_S", "900"))
_GIT_SHA_FILE = Path("/app/GIT_SHA")


def _running_git_sha() -> str | None:
    """The commit this image was built from — AQ_GIT_SHA env (build-arg -> ENV), with the baked
    /app/GIT_SHA file as fallback. None (honest 'unknown') rather than a fabricated value."""
    sha = os.environ.get("AQ_GIT_SHA")
    if sha:
        return sha
    try:
        if _GIT_SHA_FILE.is_file():
            return _GIT_SHA_FILE.read_text().strip() or None
    except Exception:
        return None
    return None


def _loop_heartbeat_snapshot() -> dict[str, Any] | None:
    """Fail-safe read of the deploy-hygiene cycle heartbeat (schema/028). NEVER raises: a DB blip
    must not 500 /health. Returns None when unconfigured / unreachable / no heartbeat yet.

    Age is computed IN THE DATABASE (now() - last_cycle_at), never from the process clock — the
    same discipline as the beat() deadline: a skewed API-container clock cannot forge freshness."""
    dsn = os.environ.get("AQ_MGMT_DB_URL") or os.environ.get("AQ_DB_URL")
    if not dsn:
        return None
    try:
        import psycopg2

        conn = psycopg2.connect(dsn, connect_timeout=3)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT last_cycle_at, cycle_count, "
                    "EXTRACT(EPOCH FROM (now() - last_cycle_at)) "
                    "FROM loop_heartbeat WHERE singleton = true"
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        last_cycle_at, cycle_count, secs = row
        return {
            "last_cycle_at": last_cycle_at.isoformat() if last_cycle_at else None,
            "cycle_count": int(cycle_count) if cycle_count is not None else None,
            "seconds_since_last_cycle": float(secs) if secs is not None else None,
        }
    except Exception:
        return None


@app.get("/health")
def health() -> dict[str, Any]:
    """Version + liveness, so 'what is deployed' and 'is it actually turning' are both observable.

    ``git_sha`` answers what is deployed; ``cycling`` answers whether a real DECIDE cycle completed
    within the freshness threshold — NOT merely that the process is up. A crash-looping-but-up loop
    reads ``cycling: false`` here, which is exactly the ~27h-down signal that used to go unnoticed.
    ``ok`` remains the endpoint-liveness flag (this handler answered); read ``cycling`` for the loop.
    """
    hb = _loop_heartbeat_snapshot()
    secs = hb["seconds_since_last_cycle"] if hb else None
    cycling = secs is not None and secs <= _CYCLING_THRESHOLD_S
    return {
        "ok": True,
        "kit": "ralph_control_management",
        "git_sha": _running_git_sha(),
        "last_cycle_at": hb["last_cycle_at"] if hb else None,
        "cycle_count": hb["cycle_count"] if hb else None,
        "seconds_since_last_cycle": secs,
        "cycling": cycling,
        "cycling_threshold_seconds": _CYCLING_THRESHOLD_S,
        "replication_override_env": OVERRIDE_ENV,
        "replication_override_enabled": override_enabled(),
        "merge_gate": "manager_gated",
    }


@app.get("/health/config")
def health_config() -> dict[str, Any]:
    """The bounded instance config this replica is actually running with (#4834 Step 2).

    Echoes ONLY the allowlisted config keys present in the environment — the observable proof
    that a ``copy_with_modifications`` config delta was applied into the replica. It never
    exposes secrets/gate/host env (those are not in the allowlist), so this endpoint cannot leak
    credentials or reveal the replication controls.
    """
    import os as _os

    return {
        "ok": True,
        "allowlist": sorted(CONFIG_ALLOWLIST),
        "instance_config": {
            k: _os.environ[k] for k in sorted(CONFIG_ALLOWLIST) if k in _os.environ
        },
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


class CommTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str | None = None
    handle: str | None = None


class CommPublishIn(BaseModel):
    """The authenticated publish request (#4834 comms Phase 0, design §4.1/§8.2).

    Identity is DERIVED from the credential, so this model deliberately has NO from_handle /
    sender_name / origin_instance_id / principal_id fields — ``extra="forbid"`` REJECTS any attempt
    to claim one (a 422), which is the structural anti-spoofing guard. The caller controls only the
    routing + content, never who it is.
    """

    model_config = ConfigDict(extra="forbid")
    channel: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    target: CommTarget | None = None
    correlation_id: str | None = None
    in_reply_to: str | None = None
    idempotency_key: str | None = None
    expires_at: str | None = None


@app.post("/api/agent-comms")
def publish_comm(body: CommPublishIn, request: Request) -> dict[str, Any]:
    """Authenticated, ACL-enforced, fail-closed publish of a versioned envelope (design §4/§8).

    Replaces the old unauthenticated demo POST (now deprecated). The pipeline:
      1. AUTHENTICATE the credential and DERIVE origin_instance_id + principal_id from it — a caller
         cannot spoof another principal by naming it (claimed identity fields are rejected by the
         request model's ``extra=forbid``).
      2. ACL: the principal may publish this kind only to channels its lineage allows (cross-instance
         denial).
      3. FAIL CLOSED: if a durable DB was configured but is unavailable, 503 — never an ephemeral ack.
      4. Idempotency: a repeated idempotency_key returns the original with no duplicate effect.

    GET /api/agent-comms still returns the legacy row projection so the React poll is unbroken.
    """
    principal = comms_auth.authenticate(request.headers)
    target = body.target or CommTarget()
    comms_auth.authorize_publish(
        principal, channel=body.channel, kind=body.kind,
        target_instance_id=target.instance_id,
    )

    # FAIL CLOSED (design §2.2/§8.5): a configured-but-unavailable durable store must not accept a
    # write from the process-local fallback and report durable acceptance.
    if durability_required() and getattr(store, "durability_required", False) \
            and store.backing != "postgres":
        raise StoreUnavailable("durable comms store required but unavailable")

    try:
        envelope = build_envelope(
            origin_instance_id=principal.origin_instance_id,
            principal_id=principal.principal_id,
            channel=body.channel,
            kind=body.kind,
            payload=body.payload,
            target_instance_id=target.instance_id,
            target_handle=target.handle,
            correlation_id=body.correlation_id,
            in_reply_to=body.in_reply_to,
            idempotency_key=body.idempotency_key,
            expires_at=body.expires_at,
        )
    except EnvelopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result = store.create_envelope(envelope)
    stored = result["envelope"]
    return {
        "ok": True,
        "duplicate": result["duplicate"],
        "envelope": stored,
        # Convenience legacy-shaped view so existing clients keep working.
        "item": legacy_projection(stored, row_id=stored["id"]),
    }


@app.post("/api/replication/propose")
def propose_replication(body: ReplicationIn) -> dict[str, Any]:
    packet = body.model_dump()
    if body.modifications is None:
        packet.pop("modifications", None)
    errors = list(validate_replication_request(packet))
    # Bounded modification policy at PROPOSE-time (#4834 Step 2): reject a forbidden config/code
    # delta early, with modification-specific messages. The host RE-validates at apply time and
    # never trusts this stored packet — this is the first of the two defense-in-depth checks.
    if packet.get("mode") == "copy_with_modifications" and body.modifications:
        errors += validate_modification_packet(body.modifications)
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


# ---- Flagship observability (Track E) ---------------------------------------

@app.get("/api/flagship")
def flagship() -> dict[str, Any]:
    """The same ground-truth projection as :8080, exposed to the React console.

    Measure-query failures remain typed in ``mission.error`` with ``now=null``. They are
    deliberately not converted to zero: zero means the query succeeded and found no customers.
    """
    return flagship_state()


def _require_approval_token(request: Request) -> None:
    expected = os.environ.get("AQ_APPROVAL_TOKEN", "").strip()
    supplied = request.headers.get("X-AQ-Approval-Token", "").strip()
    if not expected or not supplied or not compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="approval token required or rejected")


def _gate_decision(work_id: int, decision: str) -> dict[str, Any]:
    import psycopg2
    import psycopg2.extras

    dsn = os.environ.get("AQ_DB_URL")
    if not dsn:
        raise HTTPException(status_code=503, detail="AQ_DB_URL is not configured")
    with psycopg2.connect(dsn) as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        if decision == "approve":
            cur.execute(
                "UPDATE work SET status='pending', approved_at=now() "
                "WHERE id=%s AND status='awaiting_human' RETURNING *",
                (work_id,),
            )
        else:
            cur.execute(
                "UPDATE work SET status='abandoned', approved_at=NULL "
                "WHERE id=%s AND status='awaiting_human' RETURNING *",
                (work_id,),
            )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="gate item changed or does not exist")
        if decision == "approve":
            assert_valid_approval(row)
    return {"ok": True, "id": work_id, "decision": decision}


@app.post("/api/flagship/gate/{work_id}/approve")
def approve_flagship_gate(work_id: int, request: Request) -> dict[str, Any]:
    _require_approval_token(request)
    return _gate_decision(work_id, "approve")


@app.post("/api/flagship/gate/{work_id}/reject")
def reject_flagship_gate(work_id: int, request: Request) -> dict[str, Any]:
    _require_approval_token(request)
    return _gate_decision(work_id, "reject")


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
    plan_id: str | None = None
    goal_reached: bool | None = None


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


class GovernanceSelectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: str
    effect: str
    scope: dict[str, Any] | None = None
    promotion_transition_id: int
    plan_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    environment: dict[str, Any]
    evidence_ref: str = Field(min_length=1)


class PlanAuthorizationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    global_plan_id: str = Field(min_length=1)
    work_id: int = Field(gt=0)
    plan: dict[str, Any]


class GroundingContextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: dict[str, Any]
    attestation: dict[str, Any]


class GroundingObservationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: str
    effect: str
    scope: dict[str, Any] | None = None
    context_id: str = Field(min_length=1)
    test_kind: str
    evidence_ref: str = Field(min_length=1)
    expected_direction: str = "increase"
    observed_delta: float
    noise_tolerance: float = Field(default=0.0, ge=0.0)
    evidence_payload: dict[str, Any]


class AcquisitionAttestationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acquisition_id: int
    run_id: int
    proposal_index: int = Field(default=0, ge=0)


class ResolutionAttestationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prediction_id: int
    run_id: int


class PlanOutcomeAttestationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: int = Field(gt=0)


class GovernancePromotionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: str
    effect: str
    scope: dict[str, Any] | None = None
    authorization_context_id: str = Field(min_length=1)
    review_evidence_ref: str = Field(min_length=1)


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
def assess_plan(body: PlanIn, request: Request) -> dict[str, Any]:
    """The planning check: only promoted rules carry authority.

    Provisional/demoted rules are invisible to ordinary planning.  They may be consulted only
    when the caller explicitly declares a bounded experiment, and are labelled non-authoritative.
    """
    if getattr(causal, "governance", None) is not None:
        return causal.assess_plan(body.steps, bounded_experiment=body.bounded_experiment)
    return causal.assess_plan(body.steps)


def _require_evaluator_authorization(request: Request) -> None:
    import secrets
    configured = os.environ.get("AQ_EVALUATOR_TOKEN", "")
    supplied = request.headers.get("x-aq-evaluator-token", "")
    if not configured:
        raise HTTPException(status_code=503, detail="AQ_EVALUATOR_TOKEN is not configured")
    if not secrets.compare_digest(configured, supplied):
        raise HTTPException(status_code=403, detail="independent evaluator authorization required")


def _require_evaluator_trigger_authorization(request: Request) -> None:
    import secrets
    configured = os.environ.get("AQ_EVALUATOR_TRIGGER_TOKEN", "")
    supplied = request.headers.get("x-aq-evaluator-trigger-token", "")
    if not configured:
        raise HTTPException(status_code=503, detail="AQ_EVALUATOR_TRIGGER_TOKEN is not configured")
    if not secrets.compare_digest(configured, supplied):
        raise HTTPException(status_code=403, detail="evaluator trigger authorization required")


def _require_evidence_authorization(request: Request) -> None:
    import secrets
    configured = os.environ.get("AQ_GOVERNANCE_EVIDENCE_TOKEN", "")
    supplied = request.headers.get("x-aq-governance-evidence-token", "")
    if not configured:
        raise HTTPException(status_code=503, detail="AQ_GOVERNANCE_EVIDENCE_TOKEN is not configured")
    if not secrets.compare_digest(configured, supplied):
        raise HTTPException(status_code=403, detail="trusted governance evidence required")


def _require_plan_decision_authorization(request: Request) -> str:
    import secrets
    configured = os.environ.get("AQ_GOVERNANCE_DECISION_TOKEN", "")
    supplied = request.headers.get("x-aq-governance-decision-token", "")
    instance_id = os.environ.get("AQ_INSTANCE_ID", "").strip()
    if not configured or not instance_id:
        raise HTTPException(status_code=503, detail="plan decision boundary is not configured")
    if not secrets.compare_digest(configured, supplied):
        raise HTTPException(status_code=403, detail="plan decision authorization required")
    return instance_id


@app.post("/api/causal/record-outcome")
def record_outcome(body: OutcomeIn, request: Request) -> dict[str, Any]:
    """Record an act outcome as surprise on the governing edge; returns a GATED update proposal."""
    ident = causal_edge_identity({"cause": body.cause, "effect": body.effect, "scope": body.scope or {}})
    s = causal_surprise(body.predicted_certainty, body.actual_success)
    governed = None
    gov = getattr(causal, "governance", None)
    if gov is not None and body.environment is not None and body.evidence_ref and body.observed_delta is not None:
        _require_evidence_authorization(request)
        if gov.checked_withdrawal_available():
            raise HTTPException(status_code=410, detail=(
                "legacy promoter-authored outcome retired; completed checked runs are consumed by "
                "the independent evaluator outcome endpoint"))
        # Library-only compatibility: standalone schemas have no container principals or checked
        # outbox. This never runs in a migrated deployment.
        from management.api.principle_governance import GovernanceError
        try:
            environment_result = gov.record_environment_test(
                ident, body.environment, body.evidence_ref, body.expected_direction,
                body.observed_delta, body.noise_tolerance, "aq-live-outcome")
            plan_result = None
            if body.plan_id is not None or body.goal_reached is not None:
                if body.plan_id is None or body.goal_reached is None:
                    raise GovernanceError("plan usefulness outcome requires plan_id and goal_reached")
                plan_result = gov.record_plan_outcome(
                    ident, plan_id=body.plan_id, goal_reached=body.goal_reached,
                    evidence_ref=body.evidence_ref, environment=body.environment)
            governed = {"automatic_demotion": bool(environment_result.get("automatic_demotion")
                         or (plan_result or {}).get("automatic_demotion")),
                        "status": (plan_result or environment_result).get("status"),
                        "trigger": ("contradiction" if environment_result.get("automatic_demotion")
                                    else "unproductivity" if (plan_result or {}).get("automatic_demotion")
                                    else None),
                        "environment_test": environment_result, "plan_outcome": plan_result}
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


@app.post("/api/causal/governance/authorize-plan")
def governance_authorize_plan(body: PlanAuthorizationIn, request: Request) -> dict[str, Any]:
    """One narrow authenticated transaction derives and commits the pre-ACT disposition."""
    instance_id = _require_plan_decision_authorization(request)
    if not body.global_plan_id.startswith(instance_id + "/plan/"):
        raise HTTPException(status_code=409, detail="global plan belongs to another instance")
    from management.api.principle_governance import GovernanceError
    try:
        return _governance().authorize_plan(body.global_plan_id, body.work_id, body.plan)
    except GovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/causal/governance/select")
def governance_select(body: GovernanceSelectionIn, request: Request) -> dict[str, Any]:
    """Append the pre-ACT receipt proving an exact promoted rule governs the chosen plan."""
    _require_evidence_authorization(request)
    from management.api.principle_governance import GovernanceError
    edge = {"cause": body.cause, "effect": body.effect, "scope": body.scope or {}}
    try:
        usage_id = _governance().record_plan_use(
            edge, promotion_transition_id=body.promotion_transition_id,
            plan_id=body.plan_id, goal_id=body.goal_id, environment=body.environment,
            evidence_ref=body.evidence_ref)
        return {"ok": True, "usage_id": usage_id}
    except GovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/causal/governance/context")
def governance_context(body: GroundingContextIn, request: Request) -> dict[str, Any]:
    _require_evaluator_authorization(request)
    try:
        context_id = _governance().register_execution_context(body.environment, body.attestation)
        return {"ok": True, "context_id": context_id}
    except Exception as exc:
        from management.api.principle_governance import GovernanceError
        if isinstance(exc, GovernanceError):
            raise HTTPException(status_code=409, detail=str(exc))
        raise


@app.post("/api/causal/governance/observation")
def governance_observation(body: GroundingObservationIn, request: Request) -> dict[str, Any]:
    _require_evaluator_authorization(request)
    edge = {"cause": body.cause, "effect": body.effect, "scope": body.scope or {}}
    try:
        return {"ok": True, **_governance().attest_grounding_observation(
            edge, context_id=body.context_id, test_kind=body.test_kind,
            evidence_ref=body.evidence_ref, expected_direction=body.expected_direction,
            observed_delta=body.observed_delta, noise_tolerance=body.noise_tolerance,
            evidence_payload=body.evidence_payload)}
    except Exception as exc:
        from management.api.principle_governance import GovernanceError
        if isinstance(exc, GovernanceError):
            raise HTTPException(status_code=409, detail=str(exc))
        raise


@app.post("/api/causal/governance/attest-acquisition")
def governance_attest_acquisition(body: AcquisitionAttestationIn,
                                  request: Request) -> dict[str, Any]:
    _require_evaluator_authorization(request)
    return {"ok": True, "edge_id": _governance().attest_acquisition(
        body.acquisition_id, body.run_id, body.proposal_index)}


@app.post("/api/causal/governance/attest-resolution")
def governance_attest_resolution(body: ResolutionAttestationIn,
                                 request: Request) -> dict[str, Any]:
    _require_evaluator_authorization(request)
    return {"ok": True, "edge_id": _governance().attest_prediction_resolution(
        body.prediction_id, body.run_id)}


@app.post("/api/causal/governance/attest-plan-outcome")
def governance_attest_plan_outcome(body: PlanOutcomeAttestationIn,
                                   request: Request) -> dict[str, Any]:
    """Narrow trigger only: PostgreSQL derives every outcome and withdrawal predicate."""
    _require_evaluator_trigger_authorization(request)
    gov = _governance()
    primary = gov.attest_plan_outcome(body.run_id)
    backlog = gov.consume_pending_plan_outcomes(100)
    return {"ok": True, **primary, "backlog_consumed": backlog.get("consumed", 0)}


@app.post("/api/causal/governance/test")
def governance_test(body: GovernanceTestIn, request: Request) -> dict[str, Any]:
    """Record trusted validation; new-domain refutation demotes inline."""
    _require_evidence_authorization(request)
    gov = _governance()
    if gov.checked_withdrawal_available():
        raise HTTPException(status_code=410, detail=(
            "legacy promoter-authored validation retired; register an evaluator context and use "
            "/api/causal/governance/observation"))
    from management.api.principle_governance import GovernanceError
    edge = {"cause": body.cause, "effect": body.effect, "scope": body.scope or {}}
    try:
        return {"ok": True, **gov.record_environment_test(
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
    from management.api.principle_governance import GovernanceError
    edge = {"cause": body.cause, "effect": body.effect, "scope": body.scope or {}}
    try:
        tid = _governance().promote_grounded(
            edge, authorization_context_id=body.authorization_context_id,
            review_evidence_ref=body.review_evidence_ref)
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
