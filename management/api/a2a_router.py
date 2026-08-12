"""The SEPARATE, flag-gated ``/a2a`` façade router (#4834 comms Phase 5, design §7 / §10 Phase 5).

LAND INERT — this is a NEW external-facing network surface, so it is FLAG-GATED and DEFAULT-OFF:
``a2a_enabled()`` is False unless ``AQ_A2A_ENABLE`` is explicitly truthy. ``app.py`` mounts this router
ONLY when the flag is on, so a default checkout and the live deployment expose NO ``/a2a`` and NO
``/.well-known/agent-card.json`` (they return 404). Kevin is surfaced before any live enablement.

It is a TRANSLATION façade over the SAME typed queues — it reuses the Phase-0 authenticator
(server-derived identity + ACL), the Phase-2 payload validators (artifact-digest-only), and the
Phase-3 inert inbox. It adds NO authority path:

  * ``message/send`` with a *publish* skill  -> ``authorize_publish`` -> ``store.create_envelope``
    (an untrusted CLAIM, exactly like native ``POST /api/agent-comms``).
  * ``message/send`` with the *work* skill    -> ``authorize_inbox_deliver`` -> an INERT inbox
    ``work.request`` (exactly like native ``POST /api/agent-comms/inbox``), left for the separate,
    default-off importer + every downstream gate.
  * ``tasks/get`` / ``tasks/list`` / ``tasks/cancel`` are PER-PRINCIPAL scoped: a principal sees/acts
    on ONLY the envelopes whose server-derived ``principal_id`` equals its own (cross-principal ->
    TaskNotFound, never a leak). Replay + cancel are idempotent (idempotency on ``messageId``).

THE INVARIANT: nothing over ``/a2a`` satisfies an AQ gate. A "completed" Task is transport state; an
inbound work Task lands untrusted + gated. Proven by ``tests/test_a2a_invariant.py``.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from management.api.a2a_translate import (
    A2A_PROTOCOL_VERSION,
    A2A_TASK_NOT_CANCELABLE,
    A2A_TASK_NOT_FOUND,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    A2AError,
    build_agent_card,
    envelope_to_task,
    message_to_payload,
    negotiate_version,
    resolve_skill,
)
from management.api.comms_auth import AuthError, CommsAuthenticator, Principal
from management.api.comms_envelope import EnvelopeError, build_envelope, durability_required
from management.api.store import StoreUnavailable

# Reasonable request-body cap for the JSON-RPC endpoint (mirrors the native inbox body cap).
MAX_A2A_BODY_BYTES = 128 * 1024


def a2a_enabled(env: dict[str, str] | None = None) -> bool:
    """True ONLY when the operator has EXPLICITLY enabled the A2A façade (default OFF).

    Land-inert contract: unset / "0" / "false" / "" => the ``/a2a`` routes are never mounted, so a
    default checkout and the live deployment expose no new surface. Only an explicit truthy value
    flips it on; even then the façade is translation-only and grants no authority."""
    source = env if env is not None else os.environ
    raw = (source.get("AQ_A2A_ENABLE") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _rpc_error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _rpc_result(id_: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _self_and_parent() -> tuple[str, str | None]:
    self_instance_id = (os.environ.get("AQ_INSTANCE_ID") or "").strip()
    parent_instance_id = (os.environ.get("AQ_COMMS_PARENT_INSTANCE_ID") or "").strip() or None
    return self_instance_id, parent_instance_id


def _assert_durable(store: Any) -> None:
    """Fail closed (design §2.2/§8.5): a configured-but-unavailable durable store must never accept a
    write from the process-local fallback and report acceptance. Same rule as the native endpoints."""
    if durability_required() and getattr(store, "durability_required", False) \
            and getattr(store, "backing", None) != "postgres":
        raise StoreUnavailable("durable comms store required but unavailable")


def _owned(envelope: dict[str, Any], principal: Principal) -> bool:
    """Per-principal scoping: an envelope belongs to the principal whose SERVER-DERIVED principal_id
    minted it. This is the cross-principal read/cancel guard (a principal never sees another's task)."""
    return envelope.get("principal_id") == principal.principal_id


def _find_owned(store: Any, task_id: Any, principal: Principal) -> dict[str, Any]:
    if not isinstance(task_id, str) or not task_id:
        raise A2AError(JSONRPC_INVALID_PARAMS, "params.id (task id) is required")
    for env in store.envelopes():
        if env.get("id") == task_id:
            # Scope check AFTER match: a cross-principal id returns TaskNotFound, never a different
            # error that would confirm the task exists to a non-owner (no existence oracle).
            if not _owned(env, principal):
                raise A2AError(A2A_TASK_NOT_FOUND, f"task {task_id!r} not found")
            return env
    raise A2AError(A2A_TASK_NOT_FOUND, f"task {task_id!r} not found")


def _cancel_marker_for(store: Any, task_id: str, principal: Principal) -> bool:
    """True iff the principal already recorded a scoped cancellation request for ``task_id`` (so cancel
    is idempotent as a projection, not a mutable flag)."""
    for env in store.envelopes():
        if env.get("kind") != "work.request" or not _owned(env, principal):
            continue
        if (env.get("payload") or {}).get("cancel_of") == task_id:
            return True
    return False


def _project(store: Any, envelope: dict[str, Any], principal: Principal) -> dict[str, Any]:
    verification = store.verifications().get(envelope.get("id"))
    import_state = store.imports().get(envelope.get("id"))
    canceled = _cancel_marker_for(store, envelope.get("id"), principal)
    return envelope_to_task(
        envelope, verification=verification, import_state=import_state, canceled=canceled)


# ---------------------------------------------------------------------------
# JSON-RPC method handlers. Each takes (store, auth-derived principal, params) and returns a result
# dict or raises A2AError. Auth + version negotiation happen in the dispatcher before these run.
# ---------------------------------------------------------------------------
def _handle_message_send(store: Any, auth: CommsAuthenticator, principal: Principal,
                         params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise A2AError(JSONRPC_INVALID_PARAMS, "params must be an object")
    message = params.get("message")
    if not isinstance(message, dict):
        raise A2AError(JSONRPC_INVALID_PARAMS, "params.message is required")
    skill_id, spec = resolve_skill(params, message)
    payload = message_to_payload(skill_id, spec, message)
    kind = spec["kind"]
    # Idempotent replay: the A2A messageId is the idempotency key, so a re-sent message returns the
    # ORIGINAL task with no duplicate effect (design §10 Phase 5 "task replay idempotent").
    message_id = message.get("messageId")
    idem = message_id if isinstance(message_id, str) and message_id else None

    if spec["route"] == "publish":
        channel = f"instance/{principal.origin_instance_id}/a2a"
        # SAME ACL as native publish: the principal may publish this kind only to its own channel.
        auth.authorize_publish(principal, channel=channel, kind=kind)
        _assert_durable(store)
        try:
            envelope = build_envelope(
                origin_instance_id=principal.origin_instance_id,
                principal_id=principal.principal_id,
                channel=channel, kind=kind, payload=payload,
                correlation_id=(params.get("metadata") or {}).get("contextId")
                if isinstance(params.get("metadata"), dict) else None,
                idempotency_key=idem, trust="untrusted_claim")
        except EnvelopeError as exc:
            raise A2AError(JSONRPC_INVALID_PARAMS, str(exc))
        stored = store.create_envelope(envelope)["envelope"]
        return _project(store, stored, principal)

    # route == "inbox": land an INERT untrusted work.request, exactly like native /inbox.
    self_instance_id, parent_instance_id = _self_and_parent()
    target_instance_id = self_instance_id or None
    # SAME receiver ACL as native inbox delivery: only the true parent lineage may work.request this
    # replica; wrong-parent / wrong-target / self-delivery are refused (design §8.4).
    auth.authorize_inbox_deliver(
        principal, self_instance_id=self_instance_id, parent_instance_id=parent_instance_id,
        target_instance_id=target_instance_id, kind=kind)
    _assert_durable(store)
    channel = f"instance/{self_instance_id}/inbox" if self_instance_id else "instance/unknown/inbox"
    try:
        envelope = build_envelope(
            origin_instance_id=principal.origin_instance_id,
            principal_id=principal.principal_id,
            channel=channel, kind=kind, payload=payload,
            target_instance_id=target_instance_id, target_handle="replica",
            idempotency_key=idem, trust="untrusted_claim")
    except EnvelopeError as exc:
        raise A2AError(JSONRPC_INVALID_PARAMS, str(exc))
    envelope["delivery"] = "delivered"  # SAME marker the native inbox stamps (importer guard reads it)
    stored = store.create_envelope(envelope)["envelope"]
    return _project(store, stored, principal)


def _handle_tasks_get(store: Any, auth: CommsAuthenticator, principal: Principal,
                      params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise A2AError(JSONRPC_INVALID_PARAMS, "params must be an object")
    env = _find_owned(store, params.get("id"), principal)
    return _project(store, env, principal)


def _handle_tasks_list(store: Any, auth: CommsAuthenticator, principal: Principal,
                       params: dict[str, Any]) -> dict[str, Any]:
    # Per-principal scoping: ONLY the caller's own envelopes are projected as tasks.
    tasks = [_project(store, env, principal)
             for env in store.envelopes() if _owned(env, principal)]
    return {"tasks": tasks, "count": len(tasks)}


def _handle_tasks_cancel(store: Any, auth: CommsAuthenticator, principal: Principal,
                         params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise A2AError(JSONRPC_INVALID_PARAMS, "params must be an object")
    env = _find_owned(store, params.get("id"), principal)
    task_id = env.get("id")
    # Only a genuine long-running work task is cancelable; a terminal claim is not (A2A semantics).
    if env.get("kind") != "work.request":
        raise A2AError(A2A_TASK_NOT_CANCELABLE,
                       f"task {task_id!r} (kind {env.get('kind')!r}) is not cancelable")
    # Idempotent: if already canceled by this principal, return the canceled task unchanged.
    if _cancel_marker_for(store, task_id, principal):
        return _project(store, env, principal)
    # A cancellation is a GATED PROPOSAL, never a process signal / kill: store a work.request with
    # cancel_of via the SAME inert inbox path (same untrust, same downstream gates).
    self_instance_id, parent_instance_id = _self_and_parent()
    auth.authorize_inbox_deliver(
        principal, self_instance_id=self_instance_id, parent_instance_id=parent_instance_id,
        target_instance_id=self_instance_id or None, kind="work.request")
    _assert_durable(store)
    from management.api.comms_workrequest import build_work_request

    payload = build_work_request(goal=f"cancel task {task_id}", cancel_of=task_id)
    channel = f"instance/{self_instance_id}/inbox" if self_instance_id else "instance/unknown/inbox"
    cancel_env = build_envelope(
        origin_instance_id=principal.origin_instance_id, principal_id=principal.principal_id,
        channel=channel, kind="work.request", payload=payload,
        target_instance_id=self_instance_id or None, target_handle="replica",
        correlation_id=task_id, idempotency_key=f"cancel:{task_id}", trust="untrusted_claim")
    cancel_env["delivery"] = "delivered"
    store.create_envelope(cancel_env)
    return _project(store, env, principal)


_METHODS: dict[str, Callable[..., dict[str, Any]]] = {
    "message/send": _handle_message_send,
    "tasks/get": _handle_tasks_get,
    "tasks/list": _handle_tasks_list,
    "tasks/cancel": _handle_tasks_cancel,
}


def build_a2a_router(store_getter: Callable[[], Any],
                     auth_getter: Callable[[], CommsAuthenticator]) -> APIRouter:
    """Build the ``/a2a`` + agent-card router. Getters resolve the live store + authenticator at call
    time so a test (or the app) that swaps the module globals is honoured."""
    router = APIRouter()

    @router.get("/.well-known/agent-card.json")
    def agent_card(request: Request) -> dict[str, Any]:
        """A2A discovery (public, per the spec). The card advertises ONLY implemented skills."""
        base = str(request.base_url).rstrip("/") + "/a2a"
        instance_id = (os.environ.get("AQ_INSTANCE_ID") or "").strip() or None
        return build_agent_card(base_url=base, instance_id=instance_id)

    @router.post("/a2a")
    async def a2a_jsonrpc(request: Request) -> JSONResponse:
        """The A2A JSON-RPC 2.0 endpoint. Auth is HTTP-level (bearer) and happens BEFORE any body
        translation. Method errors are returned as JSON-RPC error objects (HTTP 200); credential
        failures surface as HTTP 401/403 via the AuthError handler (design §8.6 authorize-every-op)."""
        auth = auth_getter()
        store = store_getter()
        # 1) AUTHENTICATE first (headers only) — an unauthenticated call never reaches translation.
        #    AuthError propagates to the app's AuthError handler -> stable 401/403.
        principal = auth.authenticate(request.headers)

        # 2) Body cap + parse.
        raw = await request.body()
        if len(raw) > MAX_A2A_BODY_BYTES:
            return JSONResponse(status_code=413, content=_rpc_error(
                None, JSONRPC_INVALID_REQUEST, f"body exceeds {MAX_A2A_BODY_BYTES} bytes"))
        try:
            import json as _json
            body = _json.loads(raw or b"{}")
        except ValueError:
            return JSONResponse(_rpc_error(None, JSONRPC_PARSE_ERROR, "invalid JSON"))
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            return JSONResponse(_rpc_error(
                body.get("id") if isinstance(body, dict) else None,
                JSONRPC_INVALID_REQUEST, "expected a JSON-RPC 2.0 request object"))
        rpc_id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}

        # 3) Version negotiation (header or params.protocolVersion).
        try:
            requested = request.headers.get("x-a2a-version") or (
                params.get("protocolVersion") if isinstance(params, dict) else None)
            negotiate_version(requested)
        except A2AError as exc:
            return JSONResponse(_rpc_error(rpc_id, exc.code, exc.message))

        if not isinstance(method, str) or method not in _METHODS:
            return JSONResponse(_rpc_error(rpc_id, JSONRPC_METHOD_NOT_FOUND,
                                           f"method {method!r} not found"))
        try:
            result = _METHODS[method](store, auth, principal, params)
        except A2AError as exc:
            return JSONResponse(_rpc_error(rpc_id, exc.code, exc.message))
        except StoreUnavailable as exc:
            # Fail closed: report an error, store nothing (never an ephemeral ack).
            return JSONResponse(_rpc_error(rpc_id, JSONRPC_INVALID_REQUEST,
                                           f"durable store unavailable: {exc}"))
        return JSONResponse(_rpc_result(rpc_id, result))

    return router


def build_a2a_app(store: Any, auth: CommsAuthenticator) -> FastAPI:
    """A standalone FastAPI app exposing the façade over a given store + authenticator (test helper).

    Registers the AuthError handler so credential failures map to 401/403 exactly as in the real app.
    """
    app = FastAPI(title="AQ A2A façade (standalone)", version=A2A_PROTOCOL_VERSION)

    @app.exception_handler(AuthError)
    def _auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"ok": False, "error": exc.detail})

    app.include_router(build_a2a_router(lambda: store, lambda: auth))
    return app
