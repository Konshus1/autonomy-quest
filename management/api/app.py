"""FastAPI management API — Task #4407 Docker OOTB path.

Exposes the abstract surfaces so the Docker kit and prompt kit share one contract.
Wired into container/Dockerfile (COPY management/) and started by container/entrypoint.sh
(supervise_management on AQ_MGMT_PORT, default 8090) beside the loop — a management-API
crash never stops the loop. Serves an interim vanilla-JS console at `/`; the React/Vite
build is a follow-up slice (see DOCKER_OOTB_PLAN.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from ralph_portable.manager_merge_gate import validate_manager_merge_decision
from ralph_portable.replication_request import (
    OVERRIDE_ENV,
    initial_status_for_host,
    override_enabled,
    validate_replication_request,
)

app = FastAPI(title="AQ Ralph Control Management", version="0.1.0")

# Interim honest console (vanilla JS, no build step). The React/Vite build is a
# follow-up slice documented in DOCKER_OOTB_PLAN.md.
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_INDEX_HTML = _WEB_DIR / "index.html"

# In-memory stubs — uncertainty: durable tables land with schema migration later.
_WORKSTREAMS: list[dict[str, Any]] = [
    {"id": "ws-default", "title": "Default workstream", "status": "active"}
]
_TASKS: list[dict[str, Any]] = []
_COMMS: list[dict[str, Any]] = []
_REPLICATION: list[dict[str, Any]] = []
_MERGES: list[dict[str, Any]] = []


class ReplicationIn(BaseModel):
    mode: str
    mission_id: str
    requester_instance_id: str
    modifications: dict[str, Any] | None = None


class ManagerMergeIn(BaseModel):
    decision: str
    manager_handle: str
    cohort_id: str
    rationale: str
    uncertainty_note: str | None = None


@app.get("/", response_class=HTMLResponse)
def console() -> Any:
    """Serve the interim management console. Falls back to a plain notice if the
    static file is missing (e.g. partial image), so `/` never 500s."""
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
    """Snapshot of the in-memory control state (plan slice 1).

    Uncertainty: durable DB-backed state lands with the schema migration; until
    then this reflects the process-local stubs so the console has something honest
    to render. Documented in BB #2141/#2144.
    """
    return {
        "ok": True,
        "backing": "in_memory_stub",
        "merge_gate": "manager_gated",
        "replication": {
            "override_env": OVERRIDE_ENV,
            "override_enabled": override_enabled(),
            "pending": sum(1 for r in _REPLICATION if r.get("status") != "executed"),
            "total": len(_REPLICATION),
        },
        "counts": {
            "workstreams": len(_WORKSTREAMS),
            "tasks": len(_TASKS),
            "agent_comms": len(_COMMS),
            "merge_decisions": len(_MERGES),
        },
    }


@app.get("/api/workstreams")
def workstreams() -> dict[str, Any]:
    return {"items": _WORKSTREAMS}


@app.get("/api/tasks")
def tasks() -> dict[str, Any]:
    return {"items": _TASKS}


@app.get("/api/agent-comms")
def agent_comms() -> dict[str, Any]:
    return {"items": _COMMS}


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
    _REPLICATION.append(record)
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
    _MERGES.append(packet)
    return {"ok": True, "decision": packet["decision"], "record": packet}


class TaskIn(BaseModel):
    title: str = Field(min_length=1)
    workstream_id: str = "ws-default"


@app.post("/api/tasks")
def create_task(body: TaskIn) -> dict[str, Any]:
    item = {
        "id": f"task-{len(_TASKS) + 1}",
        "title": body.title,
        "workstream_id": body.workstream_id,
        "status": "open",
    }
    _TASKS.append(item)
    return {"ok": True, "item": item}
