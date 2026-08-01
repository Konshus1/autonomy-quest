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

from pathlib import Path
from typing import Any

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
# store + the fuzzy->formal planning check + surprise-driven gated promote/demote. Postgres when
# configured, else in-memory — same shape.
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


class OutcomeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: str
    effect: str
    scope: dict[str, Any] | None = None
    predicted_certainty: float
    actual_success: bool


@app.get("/api/causal/edges")
def causal_edges() -> dict[str, Any]:
    return {"items": causal.all()}


# System-managed edge fields: EARNED by the surprise loop or DERIVED by the miner, never
# caller-set. Stripped from external PUTs so a client can't launder a forced promotion
# (e.g. support_count=999) or fake provenance/mined status into the jsonb. The internal
# miner reaches causal.put() directly and is unaffected.
_SYSTEM_MANAGED_EDGE_FIELDS = frozenset(
    {"support_count", "evidence", "provenance", "evidence_run_ids", "observed_runs", "mined"}
)


@app.post("/api/causal/edges")
def put_causal_edge(edge: dict[str, Any]) -> dict[str, Any]:
    """Upsert a causal edge (identity = cause/effect/scope; validated server-side).

    Caller-supplied system-managed fields (earned support, provenance, mined flag) are dropped:
    they are set only by the surprise loop and the miner, never by an external write.
    """
    clean = {k: v for k, v in edge.items() if k not in _SYSTEM_MANAGED_EDGE_FIELDS}
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
    """The planning check: score a plan against the causal model -> a certainty profile."""
    return causal.assess_plan(body.steps)


@app.post("/api/causal/record-outcome")
def record_outcome(body: OutcomeIn) -> dict[str, Any]:
    """Record an act outcome as surprise on the governing edge; returns a GATED update proposal."""
    ident = causal_edge_identity({"cause": body.cause, "effect": body.effect, "scope": body.scope or {}})
    s = causal_surprise(body.predicted_certainty, body.actual_success)
    try:
        proposal = causal.record_evidence(ident, s)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no causal edge for identity {list(ident)}")
    return {"ok": True, "surprise": s, "proposal": proposal}
