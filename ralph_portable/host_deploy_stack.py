"""Host-ONLY gated deploy EXECUTOR — W1a gated deploy pathway (task #6087).

DANGER — READ THIS. This module is the ONLY thing that turns a guest's deploy REQUEST into a live
deploy. It imports the adapters that shell out to ``az`` (real Azure). Like ``host_replica_stack`` it
must NEVER be reachable from a guest container or any guest-reachable module — the guest only PROPOSES
a request; the HOST (this executor, driven by ``scripts/host_deploy_broker.py``) is the only
legitimate caller. The import firewall (``tests/test_deploy_import_firewall.py``) enforces that: this
module and the adapters are OUTSIDE the guest import closure.

THE FOUR HOST-SIDE GUARANTEES this executor enforces, in order, before anything goes live:

  1. FLAG-GATED — ``AQ_DEPLOY_BRIDGE`` default OFF => refuse (``status="disabled"``). Inert by default.
  2. GATED-BY-DEFAULT (authority envelope, capability class ``deploy``) — a deploy ESCALATES to a
     human (``status="escalated"``, no deploy) and emits a Slice-3 authority-change REQUEST, UNLESS
     the operator OPENED the ``deploy`` gate in their envelope. Fail-CLOSED: no envelope => fully
     gated => escalate. The actor can never open its own gate; opening is an operator edit of
     ``authority_envelope:``. The first real deploy is human-approved — correct.
  3. EXACT-SHA — the request's ``requested_sha`` must EQUAL the SHA actually built/stamped
     (``built_sha``, ground truth the host supplies). A request to deploy a different SHA than was
     built FAILS CLOSED (``status="sha_mismatch"``, no deploy). Never "latest".
  4. RECEIPT + ROLLBACK — a successful deploy records a receipt (SHA, target, revision, URL,
     timestamp, previous revision) shaped toward the V2 WorkContract/receipt (BB #2716). A deploy is
     classified ``reversible`` iff its adapter ``supports_rollback`` (ACA revisions do), else
     ``irreversible_external`` — honestly. ``rollback_deploy`` redeploys the recorded previous
     revision host-side.

THE CREDENTIAL IS HOST-ONLY. It never rides the request (rejected by ``deploy_request``), it is absent
from the act sandbox env (``runner.executor._AGENT_ENV_NAMES`` is a deny-by-default allowlist naming
no deploy credential), and it is guest-forbidden by the modification allowlist. This executor never
reads or forwards it — ``az`` authenticates from the host's own login.

Flag OFF (default) => this module is never consulted; nothing existing imports it, so a flag-off
checkout is byte-identical.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from ralph_portable import deploy_adapters
from ralph_portable.deploy_request import (
    FLAG,
    enabled,
    normalize_sha,
    validate_deploy_request,
)

log = logging.getLogger(__name__)

RECEIPT_VERSION = "deploy-receipt-v1"

# Deploy outcome statuses (the executor never raises for an operational refusal — it returns a record).
STATUS_DISABLED = "disabled"        # the AQ_DEPLOY_BRIDGE flag is OFF
STATUS_INVALID = "invalid"          # the request failed shape/safety validation
STATUS_ESCALATED = "escalated"      # the deploy gate is closed -> human approval required, no deploy
STATUS_SHA_MISMATCH = "sha_mismatch"  # requested_sha != built_sha -> fail closed, no deploy
STATUS_DEPLOY_FAILED = "deploy_failed"  # the adapter tried and failed
STATUS_DEPLOYED = "deployed"        # a live deploy + a receipt

# Reversibility classification (honest, adapter-driven).
REVERSIBLE = "reversible"
IRREVERSIBLE_EXTERNAL = "irreversible_external"


@dataclass(frozen=True)
class DeployReceipt:
    """A durable record of a live deploy, shaped toward the V2 WorkContract/receipt JSON (BB #2716).

    Carries exactly what a rollback needs: the target, the adapter, the deployed SHA, the new
    revision, and the ``previous_revision`` to return to. ``reversible`` is the honest classification
    (adapter ``supports_rollback``)."""
    target: str
    adapter: str
    deployed_sha: str
    revision: str
    url: str
    previous_revision: str
    reversibility: str            # REVERSIBLE | IRREVERSIBLE_EXTERNAL
    deployed_ref: str
    mission_id: str
    requester_instance_id: str
    timestamp: str
    receipt_version: str = RECEIPT_VERSION

    @property
    def reversible(self) -> bool:
        return self.reversibility == REVERSIBLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": "deployed",
            "target": self.target,
            "adapter": self.adapter,
            "deployed_sha": self.deployed_sha,
            "deployed_ref": self.deployed_ref,
            "revision": self.revision,
            "url": self.url,
            "previous_revision": self.previous_revision,
            "reversible": self.reversible,
            "reversibility": self.reversibility,
            "mission_id": self.mission_id,
            "requester_instance_id": self.requester_instance_id,
            "timestamp": self.timestamp,
            "receipt_version": self.receipt_version,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _refused(status: str, *, reason: str, **extra: Any) -> dict[str, Any]:
    rec = {"ok": False, "executed_by": "host", "deployed": False, "status": status, "reason": reason}
    rec.update(extra)
    return rec


def _deploy_action_text(packet: Mapping[str, Any]) -> str:
    """A human-readable action string for the authority-envelope classifier. Contains the word
    "deploy the" so the code-owned keyword map classifies it as capability class ``deploy`` even
    independent of any structured flag — the gate never trusts a model risk label."""
    return (f"deploy the built artifact {packet.get('requested_sha')} to target "
            f"{packet.get('target')!r} via the {packet.get('adapter')} adapter")


def _assess_deploy_gate(packet: Mapping[str, Any], envelope, env: Mapping[str, str]):
    """The categorical authority-envelope verdict for this deploy (capability class ``deploy``).

    Resolved HOST-side against the operator's envelope. Fail-CLOSED: no envelope supplied => the
    fully-gated DEFAULT envelope => the deploy is gated (escalates). Returns an ``EnvelopeVerdict``."""
    from runner.authority_envelope import DEFAULT_ENVELOPE, assess_action_envelope
    env_obj = envelope if envelope is not None else DEFAULT_ENVELOPE
    return assess_action_envelope(_deploy_action_text(packet), env_obj)


def _build_authority_request(verdict, packet: Mapping[str, Any], built_sha: str) -> dict[str, Any]:
    """The Slice-3 authority-change REQUEST a gated deploy justifies (a typed proposal, ZERO
    authority — it neither opens the gate nor deploys). Shaped toward the V2 receipt (BB #2716)."""
    from runner import authority_request as ar

    # A minimal work-shim so we can reuse the canonical Slice-3 builder: the deploy is a single,
    # reversible-iff-adapter-supports action; its plan digest is stable over re-parks.
    reversible = _reversibility_for_packet(packet) == REVERSIBLE

    @dataclass(frozen=True)
    class _DeployWork:
        id: int | None
        plan: dict
        reversible: bool
        rationale: str

    work = _DeployWork(
        id=None,
        plan={"deploy": {"target": packet.get("target"), "adapter": packet.get("adapter"),
                         "requested_sha": packet.get("requested_sha"), "built_sha": built_sha}},
        reversible=reversible,
        rationale=str(packet.get("note") or "autonomous deploy of the stamped built artifact"))
    requests = ar.build_requests_from_verdict(
        verdict, work=work, instance_id=str(packet.get("requester_instance_id") or ""))
    return {
        "requests": [r.to_receipt() for r in requests],
        "gated_classes": list(getattr(verdict, "gated_classes", ()) or ()),
        "reasons": list(getattr(verdict, "reasons", ()) or ()),
    }


def _reversibility_for_packet(packet: Mapping[str, Any]) -> str:
    """The honest reversibility class for a deploy to this adapter's target — WITHOUT constructing a
    real adapter (used for the pre-deploy escalation record too). ``reversible`` iff the adapter type
    supports rollback; unknown adapter => ``irreversible_external`` (fail-closed)."""
    name = str(packet.get("adapter") or "").strip().lower()
    supports = {
        deploy_adapters.MockDeployAdapter.name: deploy_adapters.MockDeployAdapter.supports_rollback,
        deploy_adapters.AcaDeployAdapter.name: deploy_adapters.AcaDeployAdapter.supports_rollback,
        deploy_adapters.StaticHostDeployAdapter.name:
            deploy_adapters.StaticHostDeployAdapter.supports_rollback,
    }.get(name, False)
    return REVERSIBLE if supports else IRREVERSIBLE_EXTERNAL


def execute_deploy(
    packet: Any,
    *,
    built_sha: str,
    envelope=None,
    adapter: "deploy_adapters.DeployAdapter | None" = None,
    adapter_config: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate + gate + exact-SHA-check a guest deploy request, and deploy HOST-side when clear.

    ``built_sha`` is the GROUND-TRUTH SHA the host actually built/stamped — the request's
    ``requested_sha`` must equal it (exact-SHA, fail-closed). ``envelope`` is the operator's authority
    envelope (None => fully-gated DEFAULT => escalate). ``adapter`` may be injected (tests / a
    pre-built adapter); otherwise it is constructed from ``packet['adapter']`` + ``adapter_config``.

    Never raises for an operational refusal — returns a record whose ``status`` is one of
    ``disabled`` / ``invalid`` / ``escalated`` / ``sha_mismatch`` / ``deploy_failed`` / ``deployed``.
    """
    env = env if env is not None else dict(os.environ)

    # GUARANTEE 1 — flag-gated. Default OFF => inert.
    if not enabled(env):
        return _refused(STATUS_DISABLED,
                        reason=f"deploy bridge disabled ({FLAG} is not set); nothing was deployed")

    # Shape + safety validation (host RE-validates; never trusts a stored packet).
    if not isinstance(packet, Mapping):
        return _refused(STATUS_INVALID, reason="deploy_request must be a mapping")
    errors = validate_deploy_request(packet)
    if errors:
        return _refused(STATUS_INVALID, reason="; ".join(errors), errors=errors)

    # GUARANTEE 2 — GATED BY DEFAULT (capability class ``deploy``). Escalate unless the operator
    # OPENED the deploy gate. No silent deploy; emit the Slice-3 authority-change request.
    verdict = _assess_deploy_gate(packet, envelope, env)
    if getattr(verdict, "gated", True):
        return _refused(
            STATUS_ESCALATED,
            reason=("deploy is capability class 'deploy' and the authority envelope holds it GATED; "
                    "escalating to human approval — the operator must OPEN the deploy gate. No deploy."),
            authority_request=_build_authority_request(verdict, packet, built_sha),
            envelope_verdict=verdict.to_authority_policy(),
            requested_sha=packet.get("requested_sha"))

    # GUARANTEE 3 — EXACT-SHA. The request must ask for exactly the SHA that was built. A mismatch
    # (a request for a different / floating ref than the stamped build) FAILS CLOSED.
    want = normalize_sha(packet.get("requested_sha"))
    got = normalize_sha(built_sha)
    if not got:
        return _refused(STATUS_SHA_MISMATCH,
                        reason=f"built_sha {built_sha!r} is not an exact stamped SHA; refusing to deploy")
    if want != got:
        return _refused(
            STATUS_SHA_MISMATCH,
            reason=(f"exact-SHA mismatch: request asks to deploy {packet.get('requested_sha')!r} but the "
                    f"built/stamped artifact is {built_sha!r}. Refusing to deploy a SHA that was not built."),
            requested_sha=packet.get("requested_sha"), built_sha=built_sha)

    # Gate + SHA cleared. Resolve the adapter (injected in tests, else constructed host-side).
    target = str(packet.get("target"))
    if adapter is None:
        try:
            adapter = deploy_adapters.get_adapter(str(packet.get("adapter")), **(adapter_config or {}))
        except (ValueError, NotImplementedError) as exc:
            return _refused(STATUS_DEPLOY_FAILED, reason=f"adapter unavailable: {exc}")

    # The exact ref to deploy: an explicit image ref if given, else the stamped SHA itself.
    artifact = packet.get("artifact") or {}
    ref = artifact.get("image") if isinstance(artifact, Mapping) and artifact.get("image") \
        else str(packet.get("requested_sha"))

    # GUARANTEE 4 — deploy, then a receipt (+ the previous revision for rollback).
    try:
        outcome = adapter.deploy(target=target, ref=str(ref), sha=str(packet.get("requested_sha")))
    except Exception as exc:  # noqa: BLE001 - operational failure -> failed record, never a crash
        return _refused(STATUS_DEPLOY_FAILED, reason=f"adapter deploy raised: {exc}", target=target)
    if not getattr(outcome, "ok", False):
        return _refused(STATUS_DEPLOY_FAILED,
                        reason=f"adapter deploy failed: {getattr(outcome, 'reason', '')}", target=target)

    reversibility = REVERSIBLE if getattr(adapter, "supports_rollback", False) else IRREVERSIBLE_EXTERNAL
    receipt = DeployReceipt(
        target=target,
        adapter=getattr(adapter, "name", str(packet.get("adapter"))),
        deployed_sha=got,
        revision=outcome.revision,
        url=outcome.url,
        previous_revision=outcome.previous_revision,
        reversibility=reversibility,
        deployed_ref=outcome.deployed_ref or str(ref),
        mission_id=str(packet.get("mission_id") or packet.get("mission") or ""),
        requester_instance_id=str(packet.get("requester_instance_id") or ""),
        timestamp=_now_iso())
    return {
        "ok": True,
        "executed_by": "host",
        "deployed": True,
        "status": STATUS_DEPLOYED,
        "receipt": receipt.to_dict(),
        "envelope_verdict": verdict.to_authority_policy(),
    }


def rollback_deploy(
    receipt: Mapping[str, Any],
    *,
    adapter: "deploy_adapters.DeployAdapter | None" = None,
    adapter_config: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Host-side rollback: redeploy the ``previous_revision`` recorded in a deploy ``receipt``.

    Fail-CLOSED: refuses when the bridge is off, when the receipt names no previous revision, or when
    the deploy was classified ``irreversible_external`` (the target cannot roll back)."""
    env = env if env is not None else dict(os.environ)
    if not enabled(env):
        return _refused(STATUS_DISABLED, reason=f"deploy bridge disabled ({FLAG}); no rollback")
    if not isinstance(receipt, Mapping):
        return _refused(STATUS_INVALID, reason="receipt must be a mapping")

    if receipt.get("reversibility") == IRREVERSIBLE_EXTERNAL or receipt.get("reversible") is False:
        return _refused("rollback_unsupported",
                        reason="deploy was classified irreversible_external; the target cannot roll back")
    previous = str(receipt.get("previous_revision") or "").strip()
    if not previous:
        return _refused("no_previous_revision",
                        reason="receipt records no previous revision to roll back to (first deploy?)")

    target = str(receipt.get("target") or "")
    if adapter is None:
        try:
            adapter = deploy_adapters.get_adapter(str(receipt.get("adapter")), **(adapter_config or {}))
        except (ValueError, NotImplementedError) as exc:
            return _refused(STATUS_DEPLOY_FAILED, reason=f"adapter unavailable for rollback: {exc}")

    try:
        outcome = adapter.rollback(target=target, revision=previous)
    except Exception as exc:  # noqa: BLE001
        return _refused(STATUS_DEPLOY_FAILED, reason=f"adapter rollback raised: {exc}", target=target)
    if not getattr(outcome, "ok", False):
        return _refused(STATUS_DEPLOY_FAILED,
                        reason=f"rollback failed: {getattr(outcome, 'reason', '')}", target=target)
    return {
        "ok": True,
        "executed_by": "host",
        "status": "rolled_back",
        "target": target,
        "revision": outcome.revision,
        "url": outcome.url,
        "reason": outcome.reason,
        "timestamp": _now_iso(),
    }
