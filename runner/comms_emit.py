"""Replica-side comms emit — bounded, FAIL-SAFE, FLAG-GATED, default-OFF (#4834 comms Phase 2).

The AQ loop calls this at lifecycle points to publish bounded ``status.report`` / ``experiment.progress``
/ ``experiment.result`` envelopes to its OWN local durable outbox, through the authenticated Phase-0
comms API on loopback. It is the ONLY loop touch, and it obeys the same discipline as the causal-consult
/ self-correction / heartbeat hooks:

  * FAIL-SAFE: every public entry point swallows ALL exceptions. A comms-emit failure — API down, DB
    outage, bad token, network refusal, malformed payload — must NEVER crash, block, or alter the loop.
    It returns ``None`` and the loop proceeds exactly as if the emit had not happened.
  * FLAG-GATED, default OFF: unless ``AQ_COMMS_EMIT`` is truthy, ``emit_enabled()`` is False and every
    entry point is a no-op BEFORE any work — so the live default loop is byte-identical until enabled.
  * NO capability, NO authority: it PUBLISHES a claim to the local outbox and stops. Identity is
    server-derived from the per-instance credential; the API stamps ``trust=untrusted_claim``. Nothing
    here can mint host_observed, change a gate, adopt, merge, or promote.

Guest-safe: imports only stdlib + the guest-safe payload schema module. It never imports the host-owned
fleet registry, Docker step, or any actuation surface (kept outside the loop's import closure).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Callable

from management.api.comms_payloads import (
    build_experiment_progress,
    build_experiment_result,
    build_status_report,
)

log = logging.getLogger("aq.comms.emit")

_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_TIMEOUT_S = 3.0


def emit_enabled(env: dict[str, str] | None = None) -> bool:
    """True only when ``AQ_COMMS_EMIT`` is explicitly truthy. Default OFF => the loop emit is a no-op
    and the live default loop stays byte-identical."""
    source = env if env is not None else os.environ
    return (source.get("AQ_COMMS_EMIT") or "").strip().lower() in _TRUTHY


def _config(env: dict[str, str]) -> dict[str, Any] | None:
    """Resolve the local publish target from env, or None if unconfigured (=> caller no-ops)."""
    token = (env.get("AQ_COMMS_INSTANCE_TOKEN") or "").strip()
    instance_id = (env.get("AQ_INSTANCE_ID") or "").strip()
    if not token or not instance_id:
        return None
    port = (env.get("AQ_MGMT_PORT") or "8090").strip()
    parent = (env.get("AQ_COMMS_PARENT_INSTANCE_ID") or "").strip()
    return {
        "url": f"http://127.0.0.1:{int(port)}/api/agent-comms",
        "token": token,
        "instance_id": instance_id,
        "parent_instance_id": parent or None,
    }


def _channel(cfg: dict[str, Any], suffix: str) -> str:
    """A lineage channel to the parent when a parent is known, else the instance-local channel. Both
    are inside the replica's OWN ACL scope (it can never publish outside its lineage)."""
    parent = cfg.get("parent_instance_id")
    if parent:
        return f"lineage/{parent}/{suffix}"
    return f"instance/{cfg['instance_id']}/local"


def _post(cfg: dict[str, Any], *, channel: str, kind: str, payload: dict[str, Any],
          idempotency_key: str | None, correlation_id: str | None,
          timeout_s: float, opener: Callable[..., bytes] | None) -> dict[str, Any] | None:
    body = {"channel": channel, "kind": kind, "payload": payload}
    if idempotency_key:
        body["idempotency_key"] = idempotency_key
    if correlation_id:
        body["correlation_id"] = correlation_id
    # Target the parent (handle only — no host path/URL). The host derives routing from the credential.
    if cfg.get("parent_instance_id"):
        body["target"] = {"instance_id": cfg["parent_instance_id"], "handle": "parent"}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        cfg["url"], data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['token']}"})
    fetch = opener or _default_post
    raw = fetch(req, timeout_s=timeout_s)
    try:
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None


def _default_post(req: urllib.request.Request, *, timeout_s: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 (loopback own API)
        return resp.read()


def _safe(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a public emit so ANY failure is swallowed (fail-safe). The loop must never notice."""

    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        env = kwargs.get("env") or os.environ
        if not emit_enabled(env):
            return None
        cfg = _config(dict(env))
        if cfg is None:
            return None
        try:
            return fn(cfg, *args, **kwargs)
        except (urllib.error.URLError, OSError, ValueError, TypeError, KeyError) as exc:
            log.debug("comms emit %s skipped (non-fatal): %s", fn.__name__, exc)
            return None
        except Exception:  # defense in depth: a comms emit can never take down the loop
            log.debug("comms emit %s skipped (non-fatal, unexpected)", fn.__name__, exc_info=True)
            return None

    wrapper.__name__ = fn.__name__
    return wrapper


# --- public, fail-safe entry points ------------------------------------------
@_safe
def emit_status_report(cfg: dict[str, Any], *, text: str, state: str | None = None,
                       idempotency_key: str | None = None, correlation_id: str | None = None,
                       extra: dict[str, Any] | None = None,
                       timeout_s: float = _DEFAULT_TIMEOUT_S,
                       opener: Callable[..., bytes] | None = None,
                       env: dict[str, str] | None = None) -> dict[str, Any] | None:
    payload = build_status_report(text=text, state=state, extra=extra)
    return _post(cfg, channel=_channel(cfg, "status"), kind="status.report", payload=payload,
                 idempotency_key=idempotency_key, correlation_id=correlation_id,
                 timeout_s=timeout_s, opener=opener)


@_safe
def emit_experiment_progress(cfg: dict[str, Any], *, text: str, progress: float | None = None,
                             idempotency_key: str | None = None, correlation_id: str | None = None,
                             extra: dict[str, Any] | None = None,
                             timeout_s: float = _DEFAULT_TIMEOUT_S,
                             opener: Callable[..., bytes] | None = None,
                             env: dict[str, str] | None = None) -> dict[str, Any] | None:
    payload = build_experiment_progress(text=text, progress=progress,
                                        correlation_id=correlation_id, extra=extra)
    return _post(cfg, channel=_channel(cfg, "experiments"), kind="experiment.progress",
                 payload=payload, idempotency_key=idempotency_key, correlation_id=correlation_id,
                 timeout_s=timeout_s, opener=opener)


@_safe
def emit_experiment_result(cfg: dict[str, Any], *, summary: str, outcome_claimed: str = "unknown",
                           artifact_refs: list[dict[str, Any]] | None = None,
                           metrics: dict[str, Any] | None = None,
                           idempotency_key: str | None = None, correlation_id: str | None = None,
                           extra: dict[str, Any] | None = None,
                           timeout_s: float = _DEFAULT_TIMEOUT_S,
                           opener: Callable[..., bytes] | None = None,
                           env: dict[str, str] | None = None) -> dict[str, Any] | None:
    payload = build_experiment_result(summary=summary, outcome_claimed=outcome_claimed,
                                      artifact_refs=artifact_refs, metrics=metrics, extra=extra)
    return _post(cfg, channel=_channel(cfg, "experiments"), kind="experiment.result",
                 payload=payload, idempotency_key=idempotency_key, correlation_id=correlation_id,
                 timeout_s=timeout_s, opener=opener)


@_safe
def emit_cycle_completed(cfg: dict[str, Any], *, cycle: Any = None, template: str | None = None,
                         workflow: str | None = None,
                         timeout_s: float = _DEFAULT_TIMEOUT_S,
                         opener: Callable[..., bytes] | None = None,
                         env: dict[str, str] | None = None) -> dict[str, Any] | None:
    """The lifecycle emit the loop calls after a completed cycle. Bounded: one ``status.report`` with
    a stable idempotency_key so an at-least-once retry does not duplicate. ``cycle`` is the loop's
    ``Cycle`` (or None for an idle cycle); only scalar, non-sensitive fields are surfaced."""
    run_id = getattr(cycle, "run_id", None)
    work_id = getattr(cycle, "work_id", None)
    if cycle is None:
        text = "loop cycle completed (idle — nothing to do)"
        idem = None  # idle cycles are frequent + interchangeable; no dedup key needed
    else:
        text = f"loop cycle completed: work #{work_id} run #{run_id}"
        idem = f"cycle:{cfg['instance_id']}:{run_id}:{work_id}"
    extra: dict[str, Any] = {}
    if template:
        extra["template"] = str(template)[:128]
    if workflow:
        extra["workflow"] = str(workflow)[:128]
    return emit_status_report(text=text, state="cycling", idempotency_key=idem,
                              extra=extra or None, timeout_s=timeout_s, opener=opener, env=env)
