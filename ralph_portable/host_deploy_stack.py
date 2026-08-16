"""Host-ONLY gated deploy EXECUTOR — W1a gated deploy pathway (task #6087).

DANGER — READ THIS. This module is the ONLY thing that turns a guest's deploy REQUEST into a live
deploy. It imports the adapters that shell out to ``az`` (real Azure). Like ``host_replica_stack`` it
must NEVER be reachable from a guest container or any guest-reachable module — the guest only PROPOSES
a request; the HOST (this executor, driven by ``scripts/host_deploy_broker.py``) is the only
legitimate caller. The import firewall (``tests/test_deploy_import_firewall.py``) enforces that: this
module and the adapters are OUTSIDE the guest import closure.

THE GUARANTEES this executor enforces, in order, before anything goes live:

  1. FLAG-GATED — ``AQ_DEPLOY_BRIDGE`` default OFF => refuse (``status="disabled"``). Inert by default.
  2. GATED-BY-DEFAULT (authority envelope, capability class ``deploy``) — a deploy ESCALATES to a
     human (``status="escalated"``, no deploy) and emits a Slice-3 authority-change REQUEST, UNLESS
     the operator OPENED the ``deploy`` gate. Fail-CLOSED: no envelope => fully gated => escalate. The
     actor can never open its own gate; opening is a STANDING, CLASS-LEVEL operator edit of
     ``authority_envelope:`` (see the Slice-3 request wording — no overclaim of a per-action bound).
  3. EXACT-SHA — the request's ``requested_sha`` must EQUAL the git SHA actually built/stamped
     (``built_sha``, ground truth the host supplies). A request to deploy a different SHA than was
     built FAILS CLOSED (``status="sha_mismatch"``). Never "latest".
  4. HOST-CANONICAL IMAGE (the guest can NOT pick the image bytes) — for an image adapter (ACA), the
     HOST resolves the git SHA to ITS OWN recorded image via an injected :class:`ImageResolver`, and
     verifies that image is (a) digest-pinned ``registry/repo@sha256:<digest>``, (b) in the host
     REGISTRY ALLOWLIST, and (c) carries the digest the host RECORDED for that SHA. Any failure fails
     CLOSED — a request never names, and a guest never influences, the deployed bytes. The request
     names a git SHA; the host decides which image that is.
  5. RECEIPT + ROLLBACK — a successful deploy records a receipt (git SHA, resolved image + digest,
     target, revision, URL, timestamp, previous revision) shaped toward the V2 WorkContract/receipt
     (BB #2716). A deploy is classified ``reversible`` iff its adapter ``supports_rollback`` (ACA
     revisions do), else ``irreversible_external``. ``rollback_deploy`` redeploys the recorded
     previous revision host-side — and is ALSO envelope-gated (a rollback is a ``deploy``-class live
     change), so it never runs a live-state change outside the gate.

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
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ralph_portable import deploy_adapters
from ralph_portable.deploy_request import (
    FLAG,
    enabled,
    normalize_sha,
    validate_deploy_request,
)

log = logging.getLogger(__name__)

RECEIPT_VERSION = "deploy-receipt-v2"  # v2: host-resolved digest-pinned image binding

# Host registry allowlist env (comma/space-separated ``registry/repo`` prefixes the host may deploy).
REGISTRY_ALLOWLIST_ENV = "AQ_DEPLOY_REGISTRY_ALLOWLIST"

# Deploy outcome statuses (the executor never raises for an operational refusal — it returns a record).
STATUS_DISABLED = "disabled"          # the AQ_DEPLOY_BRIDGE flag is OFF
STATUS_INVALID = "invalid"            # the request failed shape/safety validation
STATUS_ESCALATED = "escalated"        # the deploy gate is closed -> human approval required, no deploy
STATUS_SHA_MISMATCH = "sha_mismatch"  # requested != built, or resolved digest != recorded -> no deploy
STATUS_IMAGE_UNRESOLVED = "image_unresolved"  # host could not resolve a pinned+allowlisted image
STATUS_DEPLOY_FAILED = "deploy_failed"  # the adapter tried and failed
STATUS_DEPLOYED = "deployed"          # a live deploy + a receipt
STATUS_ROLLED_BACK = "rolled_back"

# Reversibility classification (honest, adapter-driven).
REVERSIBLE = "reversible"
IRREVERSIBLE_EXTERNAL = "irreversible_external"

#: A digest-pinned OCI reference: ``<registry>/<repo>@sha256:<64hex>``. Anything else (a tag, "latest",
#: a bare repo) is NOT digest-pinned and fails closed.
_DIGEST_REF_RE = re.compile(r"^(?P<repo>[^@\s]+)@sha256:(?P<digest>[0-9a-f]{64})$")


# --- host-canonical image resolution -----------------------------------------------------------
@dataclass(frozen=True)
class ResolvedImage:
    """A host-recorded canonical image for a git SHA. ``image`` is the digest-pinned ref the host will
    actually deploy; ``digest`` is the content digest the host RECORDED for ``git_sha`` at build time.
    The guest never supplies any of this — it is host ground truth."""
    git_sha: str
    image: str            # registry/repo@sha256:<digest>
    digest: str           # the recorded sha256 content digest (64 hex, no "sha256:" prefix)


class ImageResolver:
    """Resolve a git SHA to the host's OWN canonical image. HOST-owned; the guest never touches it.

    A concrete resolver is backed by a host build receipt / a host-controlled registry that records,
    per git SHA, the image the host itself built + pushed and its content digest."""

    def resolve(self, git_sha: str) -> "ResolvedImage | None":
        raise NotImplementedError


class RecordedImageResolver(ImageResolver):
    """An :class:`ImageResolver` backed by a host-owned ``{git_sha: {"image":..., "digest":...}}``
    record (a build-receipt store the host CI writes when it builds+pushes an image for a SHA). Pure
    lookup, no network — the record IS the host's ground truth."""

    def __init__(self, records: Mapping[str, Mapping[str, str]]) -> None:
        self._records = {normalize_sha(k) or str(k): dict(v) for k, v in (records or {}).items()}

    def resolve(self, git_sha: str) -> "ResolvedImage | None":
        rec = self._records.get(normalize_sha(git_sha))
        if not rec or not rec.get("image") or not rec.get("digest"):
            return None
        return ResolvedImage(git_sha=normalize_sha(git_sha), image=str(rec["image"]),
                             digest=str(rec["digest"]).replace("sha256:", "").strip().lower())


def registry_allowlist(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The host registry/repo prefixes a deploy may target, from ``AQ_DEPLOY_REGISTRY_ALLOWLIST``.
    Empty => nothing is allowlisted => every image resolution fails closed (safe default)."""
    source = env if env is not None else os.environ
    raw = str(source.get(REGISTRY_ALLOWLIST_ENV, "")).replace(",", " ")
    return tuple(tok for tok in (t.strip() for t in raw.split()) if tok)


def _resolve_and_verify_image(
    git_sha: str, resolver: "ImageResolver | None", allowlist: Sequence[str],
) -> tuple["ResolvedImage | None", str, str]:
    """Resolve the git SHA to a host-canonical image and verify it. Returns ``(resolved, status,
    reason)``: ``status`` is ``""`` on success, else :data:`STATUS_IMAGE_UNRESOLVED` /
    :data:`STATUS_SHA_MISMATCH`. FAIL-CLOSED on every branch — a guest can never steer this."""
    if resolver is None:
        return None, STATUS_IMAGE_UNRESOLVED, (
            "no host image resolver configured; refusing to deploy an image adapter without a "
            "host-canonical image for the git SHA (the guest must never name the image)")
    resolved = resolver.resolve(git_sha)
    if resolved is None:
        return None, STATUS_IMAGE_UNRESOLVED, (
            f"host has no recorded image for git SHA {git_sha!r}; refusing to deploy an unresolved SHA")
    m = _DIGEST_REF_RE.match(resolved.image.strip())
    if not m:
        return None, STATUS_IMAGE_UNRESOLVED, (
            f"host-resolved image {resolved.image!r} is not digest-pinned (registry/repo@sha256:<64hex>)")
    ref_digest = m.group("digest").lower()
    if ref_digest != resolved.digest.lower():
        # the image the host would deploy carries a different digest than the one the host RECORDED
        # for this SHA -> the deployed bytes are not the recorded bytes. Fail closed (reviewer HIGH #1).
        return None, STATUS_SHA_MISMATCH, (
            f"resolved image digest {ref_digest!r} != recorded digest {resolved.digest!r} for SHA "
            f"{git_sha!r}; refusing to deploy bytes that are not the host's recorded artifact")
    repo = m.group("repo")
    if not allowlist:
        return None, STATUS_IMAGE_UNRESOLVED, (
            f"no registry allowlist configured ({REGISTRY_ALLOWLIST_ENV}); refusing to deploy any image")
    if not any(repo == pref or repo.startswith(pref.rstrip("/") + "/") or repo.startswith(pref)
               for pref in allowlist):
        return None, STATUS_IMAGE_UNRESOLVED, (
            f"host-resolved image repo {repo!r} is not in the registry allowlist {list(allowlist)}; "
            "refusing to deploy from an un-allowlisted registry")
    return resolved, "", "ok"


@dataclass(frozen=True)
class DeployReceipt:
    """A durable record of a live deploy, shaped toward the V2 WorkContract/receipt JSON (BB #2716).

    ``deployed_sha`` is the git SHA; ``deployed_ref`` + ``deployed_digest`` are the host-resolved,
    digest-pinned image the host actually put live (empty for a SHA-only adapter like the mock). No
    guest string appears here. ``previous_revision`` is what a rollback returns to; ``reversibility``
    is the honest adapter classification."""
    target: str
    adapter: str
    deployed_sha: str
    deployed_ref: str
    deployed_digest: str
    revision: str
    url: str
    previous_revision: str
    reversibility: str            # REVERSIBLE | IRREVERSIBLE_EXTERNAL
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
            "deployed_digest": self.deployed_digest,
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
    """A human-readable action string for the authority-envelope classifier. Contains "deploy the" so
    the code-owned keyword map classifies it as capability class ``deploy`` even independent of any
    structured flag — the gate never trusts a model risk label."""
    return (f"deploy the built artifact at git SHA {packet.get('requested_sha')} to target "
            f"{packet.get('target')!r} via the {packet.get('adapter')} adapter")


def _rollback_action_text(receipt: Mapping[str, Any]) -> str:
    """Action string for a ROLLBACK — also a ``deploy``-class live change ("redeploy the ...")."""
    return (f"redeploy the previously-recorded revision {receipt.get('previous_revision')!r} to target "
            f"{receipt.get('target')!r} (rollback of a deploy)")


def _assess_gate(action_text: str, envelope, env: Mapping[str, str]):
    """The categorical authority-envelope verdict for a ``deploy``-class action. Fail-CLOSED: no
    envelope => the fully-gated DEFAULT => gated (escalates)."""
    from runner.authority_envelope import DEFAULT_ENVELOPE, assess_action_envelope
    return assess_action_envelope(action_text, envelope if envelope is not None else DEFAULT_ENVELOPE)


def _reversibility_for_packet(packet: Mapping[str, Any]) -> str:
    """The honest reversibility class for a deploy to this adapter's target WITHOUT constructing a real
    adapter (used for the pre-deploy escalation record too). Unknown adapter => irreversible_external."""
    name = str(packet.get("adapter") or "").strip().lower()
    supports = {
        deploy_adapters.MockDeployAdapter.name: deploy_adapters.MockDeployAdapter.supports_rollback,
        deploy_adapters.AcaDeployAdapter.name: deploy_adapters.AcaDeployAdapter.supports_rollback,
        deploy_adapters.StaticHostDeployAdapter.name:
            deploy_adapters.StaticHostDeployAdapter.supports_rollback,
    }.get(name, False)
    return REVERSIBLE if supports else IRREVERSIBLE_EXTERNAL


def _build_authority_request(verdict, packet: Mapping[str, Any], built_sha: str) -> dict[str, Any]:
    """The Slice-3 authority-change REQUEST a gated deploy justifies (a typed proposal, ZERO authority
    — it neither opens the gate nor deploys). Shaped toward the V2 receipt (BB #2716).

    LOW #3 (honesty): the ONLY mechanism that opens the gate is the STANDING, CLASS-LEVEL edit
    ``authority_envelope: deploy: open``, which authorizes EVERY deploy of the class until re-gated —
    NOT a single action. We correct the generic builder's per-action ``requested_bound`` wording so the
    request text does not overclaim a one-shot bound the mechanism cannot provide."""
    from runner import authority_request as ar

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

    honest_bound = (
        "opening 'deploy' is a STANDING, CLASS-LEVEL grant (authority_envelope: deploy: open): it "
        "authorizes ALL deploys of this class for this instance until the operator re-gates it — NOT "
        "just this one action. The host still resolves the image bytes from its own registry allowlist, "
        "so an open gate authorizes the CLASS, never a specific guest-chosen image.")
    receipts = []
    for r in requests:
        rc = r.to_receipt()
        rc["requested_bound"] = honest_bound            # LOW #3: match the actual (standing) effect
        rc["grant_is_standing_class_level"] = True
        receipts.append(rc)
    return {
        "requests": receipts,
        "gated_classes": list(getattr(verdict, "gated_classes", ()) or ()),
        "reasons": list(getattr(verdict, "reasons", ()) or ()),
    }


def execute_deploy(
    packet: Any,
    *,
    built_sha: str,
    envelope=None,
    adapter: "deploy_adapters.DeployAdapter | None" = None,
    adapter_config: Mapping[str, Any] | None = None,
    image_resolver: "ImageResolver | None" = None,
    allowlist: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate + gate + exact-SHA-check a guest deploy request, resolve the HOST-canonical image, and
    deploy HOST-side when clear.

    ``built_sha`` is the GROUND-TRUTH git SHA the host actually built/stamped — the request's
    ``requested_sha`` must equal it (exact-SHA, fail-closed). ``envelope`` is the operator's authority
    envelope (None => fully-gated DEFAULT => escalate). ``image_resolver`` + ``allowlist`` are the
    HOST's own image ground truth (a guest never supplies them); for an image adapter (ACA) the host
    resolves the git SHA to its recorded, digest-pinned, allowlisted image and deploys THAT — the
    guest can never name the bytes. ``allowlist`` defaults to ``AQ_DEPLOY_REGISTRY_ALLOWLIST``.

    Never raises for an operational refusal — returns a record whose ``status`` is one of
    ``disabled`` / ``invalid`` / ``escalated`` / ``sha_mismatch`` / ``image_unresolved`` /
    ``deploy_failed`` / ``deployed``.
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

    # GUARANTEE 2 — GATED BY DEFAULT (capability class ``deploy``). Escalate unless the operator OPENED
    # the deploy gate. No silent deploy; emit the Slice-3 authority-change request.
    verdict = _assess_gate(_deploy_action_text(packet), envelope, env)
    if getattr(verdict, "gated", True):
        return _refused(
            STATUS_ESCALATED,
            reason=("deploy is capability class 'deploy' and the authority envelope holds it GATED; "
                    "escalating to human approval — the operator must OPEN the deploy gate. No deploy."),
            authority_request=_build_authority_request(verdict, packet, built_sha),
            envelope_verdict=verdict.to_authority_policy(),
            requested_sha=packet.get("requested_sha"))

    # GUARANTEE 3 — EXACT-SHA. The request must ask for exactly the git SHA that was built.
    want = normalize_sha(packet.get("requested_sha"))
    got = normalize_sha(built_sha)
    if not got:
        return _refused(STATUS_SHA_MISMATCH,
                        reason=f"built_sha {built_sha!r} is not an exact git SHA; refusing to deploy")
    if want != got:
        return _refused(
            STATUS_SHA_MISMATCH,
            reason=(f"exact-SHA mismatch: request asks to deploy {packet.get('requested_sha')!r} but the "
                    f"built/stamped artifact is {built_sha!r}. Refusing to deploy a SHA that was not built."),
            requested_sha=packet.get("requested_sha"), built_sha=built_sha)

    # Resolve the adapter (injected in tests, else constructed host-side).
    target = str(packet.get("target"))
    if adapter is None:
        try:
            adapter = deploy_adapters.get_adapter(str(packet.get("adapter")), **(adapter_config or {}))
        except (ValueError, NotImplementedError) as exc:
            return _refused(STATUS_DEPLOY_FAILED, reason=f"adapter unavailable: {exc}")

    # GUARANTEE 4 — HOST-CANONICAL IMAGE. For an image adapter the HOST resolves the git SHA to ITS OWN
    # recorded, digest-pinned, allowlisted image and deploys THAT — the guest never names the bytes.
    # A SHA-only adapter (mock / a future static-file push) deploys the SHA itself (still host-chosen).
    resolved_image: "ResolvedImage | None" = None
    if getattr(adapter, "requires_image_ref", False):
        active_allowlist = tuple(allowlist) if allowlist is not None else registry_allowlist(env)
        resolved_image, status, reason = _resolve_and_verify_image(got, image_resolver, active_allowlist)
        if resolved_image is None:
            return _refused(status, reason=reason, target=target, requested_sha=packet.get("requested_sha"))
        ref = resolved_image.image
    else:
        ref = got  # host deploys the stamped git SHA itself; no image ref, no guest string

    # GUARANTEE 5 — deploy, then a receipt (+ the previous revision for rollback).
    try:
        outcome = adapter.deploy(target=target, ref=str(ref), sha=got)
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
        deployed_ref=(resolved_image.image if resolved_image else str(ref)),
        deployed_digest=(resolved_image.digest if resolved_image else ""),
        revision=outcome.revision,
        url=outcome.url,
        previous_revision=outcome.previous_revision,
        reversibility=reversibility,
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
    envelope=None,
    adapter: "deploy_adapters.DeployAdapter | None" = None,
    adapter_config: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Host-side rollback: redeploy the ``previous_revision`` recorded in a deploy ``receipt``.

    A rollback is itself a ``deploy``-class LIVE change, so it is ENVELOPE-GATED (LOW #2): it escalates
    unless the operator opened the ``deploy`` gate — no live-state change happens outside the gate.
    Even so it is tightly BOUNDED: it can only re-activate a revision the host already recorded in the
    receipt; it can NEVER introduce a new image, SHA, or registry. It is host-invoked only (never on a
    guest path). Fail-CLOSED: refuses when the bridge is off, when gated, when the deploy was
    ``irreversible_external``, or when the receipt names no previous revision."""
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

    # LOW #2 — envelope-gate the rollback (a deploy-class live change). Escalate if the gate is closed.
    verdict = _assess_gate(_rollback_action_text(receipt), envelope, env)
    if getattr(verdict, "gated", True):
        return _refused(
            STATUS_ESCALATED,
            reason=("rollback is a 'deploy'-class live change and the envelope holds it GATED; escalating "
                    "— the operator must OPEN the deploy gate. No rollback."),
            envelope_verdict=verdict.to_authority_policy())

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
        "status": STATUS_ROLLED_BACK,
        "target": target,
        "revision": outcome.revision,
        "url": outcome.url,
        "reason": outcome.reason,
        "timestamp": _now_iso(),
    }
