"""Isolated subscription-agent worker. It has actor credentials, never loop/governance authority."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
from decimal import Decimal
from typing import Any
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .executor import RateLimited, SubscriptionExecutor, _read_local_secret

app = FastAPI(title="AQ isolated ACT worker")
_lock = threading.Lock()
_executor: SubscriptionExecutor | None = None
_seen_nonces: dict[str, float] = {}
_nonce_lock = threading.Lock()

class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str
    output_schema: dict[str, Any] = Field(alias="schema")
    tier: str = "working"
    request_id: str = Field(min_length=8, max_length=200)

def _auth(body: RunIn, timestamp: str, nonce: str, signature: str) -> None:
    configured = _read_local_secret("AQ_ACT_RPC_TOKEN", "AQ_ACT_RPC_TOKEN_FILE")
    try:
        stamp = int(timestamp)
    except (TypeError, ValueError):
        stamp = 0
    now = time.time()
    if not configured or not nonce or abs(now - stamp) > 30:
        raise HTTPException(status_code=403, detail="isolated ACT authorization required")
    data = json.dumps(body.model_dump(by_alias=True), sort_keys=True,
                      separators=(",", ":")).encode()
    message = f"{stamp}\n{nonce}\n{hashlib.sha256(data).hexdigest()}".encode()
    expected = hmac.new(configured.encode(), message, hashlib.sha256).hexdigest()
    if not secrets.compare_digest(expected, signature):
        raise HTTPException(status_code=403, detail="isolated ACT authorization required")
    with _nonce_lock:
        for old_nonce, seen_at in list(_seen_nonces.items()):
            if now - seen_at > 60:
                del _seen_nonces[old_nonce]
        if nonce in _seen_nonces:
            raise HTTPException(status_code=403, detail="ACT request replay rejected")
        _seen_nonces[nonce] = now

def _receipt_path(request_id: str) -> tuple[Path, str]:
    root = Path(os.environ.get("AQ_ACT_RECEIPT_DIR", "/var/lib/aq/worker-receipts"))
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(request_id.encode()).hexdigest()
    return root / f"{digest}.json", digest


def _read_receipt(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except FileNotFoundError:
        return None


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    with temp.open("w") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _body_hash(body: RunIn) -> str:
    data = json.dumps(body.model_dump(by_alias=True), sort_keys=True,
                      separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def _existing_result(path: Path, body_hash: str) -> dict[str, Any] | None:
    receipt = _read_receipt(path)
    if receipt is None:
        return None
    if receipt.get("body_hash") != body_hash:
        raise HTTPException(status_code=409, detail={"kind": "dispatch_uncertain",
            "message": "request_id was reused with a different body"})
    if receipt.get("state") == "completed" and isinstance(receipt.get("envelope"), dict):
        return receipt["envelope"]
    raise HTTPException(status_code=409, detail={"kind": "dispatch_uncertain",
        "message": f"ACT request is durably {receipt.get('state','unresolved')}; do not replay"})


def _executor_instance() -> SubscriptionExecutor:
    global _executor
    if _executor is None:
        _executor = SubscriptionExecutor(
            os.environ.get("AQ_WORKER_ENGINE", "codex"),
            cwd=os.environ.get("AQ_ACT_WORKSPACE", "/workspace"),
            timeout_s=int(os.environ.get("AQ_EXECUTOR_TIMEOUT_S", "600")))
    return _executor

@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}

@app.get("/ready")
def ready() -> dict[str, bool]:
    try:
        _executor_instance()  # includes subscription-only authentication verification
    except Exception:
        raise HTTPException(status_code=503, detail="subscription worker not authenticated") from None
    return {"ok": True, "subscription_authenticated": True}

@app.post("/run")
def run(body: RunIn, x_aq_act_timestamp: str = Header(default=""),
        x_aq_act_nonce: str = Header(default=""),
        x_aq_act_signature: str = Header(default="")) -> dict[str, Any]:
    _auth(body, x_aq_act_timestamp, x_aq_act_nonce, x_aq_act_signature)
    receipt_path, _ = _receipt_path(body.request_id)
    body_hash = _body_hash(body)
    prior = _existing_result(receipt_path, body_hash)
    if prior is not None:
        return prior
    if not _lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail={"kind": "worker_busy",
            "message": "ACT worker already executing another request"})
    try:
        prior = _existing_result(receipt_path, body_hash)
        if prior is not None:
            return prior
        _write_receipt(receipt_path, {"request_id": body.request_id, "body_hash": body_hash,
                                     "state": "running", "started_at": time.time()})
        try:
            result, usage = _executor_instance().run(body.prompt, body.output_schema, body.tier)
        except RateLimited as exc:
            _write_receipt(receipt_path, {"request_id": body.request_id, "body_hash": body_hash,
                                         "state": "rate-limited-uncertain",
                                         "error": type(exc).__name__, "finished_at": time.time()})
            raise HTTPException(status_code=409, detail={"kind": "dispatch_uncertain",
                "message": "rate limit was reported after ACT dispatch; external effects are unknown"}) from None
        except Exception as exc:
            _write_receipt(receipt_path, {"request_id": body.request_id, "body_hash": body_hash,
                                         "state": "failed-uncertain", "error": type(exc).__name__,
                                         "finished_at": time.time()})
            raise HTTPException(status_code=500, detail={"kind": "dispatch_uncertain",
                "message": "ACT failed after durable dispatch; external effects are unknown"}) from None
        envelope = {"result": result, "usage": {"tokens_in": usage.tokens_in,
                    "tokens_out": usage.tokens_out, "cost_usd": str(usage.cost_usd)}}
        _write_receipt(receipt_path, {"request_id": body.request_id, "body_hash": body_hash,
                                     "state": "completed", "finished_at": time.time(),
                                     "envelope": envelope})
        return envelope
    finally:
        _lock.release()
