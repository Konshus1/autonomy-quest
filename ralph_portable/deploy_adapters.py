"""Pluggable deploy ADAPTERS — W1a gated deploy pathway (task #6087). HOST-ONLY.

DANGER — READ THIS. The real adapter here (:class:`AcaDeployAdapter`) shells out to ``az containerapp``
against a real Azure Container Apps environment: it pushes a LIVE revision. Like
``host_replica_stack``, this module must therefore NEVER be reachable from a guest container or any
guest-reachable module — a guest only ever PROPOSES a deploy request; the HOST executor
(``host_deploy_stack``) is the only legitimate caller. The import firewall
(``tests/test_deploy_import_firewall.py``) enforces that structurally: the deploy step is outside the
guest import closure, so a guest can neither reach the ``az`` shell-out nor the credential it uses.

THE CREDENTIAL IS NEVER OURS TO HOLD. The Azure credential lives ONLY in the host's ``az`` login /
service-principal environment; ``az`` reads it itself. This module NEVER accepts, reads, forwards, or
logs a credential — not on the request, not in argv, not in the environment we build for ``az`` (we
pass ``env=None`` so ``az`` inherits the host's own login; nothing credential-shaped crosses this
boundary). ``AcaDeployAdapter`` is unit-tested with an INJECTED command runner so the whole
request->deploy->receipt->rollback mechanism is proven WITHOUT real Azure or any credential.

PLUGGABLE by design: adapters share one small interface (:class:`DeployAdapter`) selected by name
(:func:`get_adapter`), so a ``static_host`` (git-push-to-Pages / static CLI) adapter can be added
later without touching the request contract or the host executor. Fail-CLOSED: an unknown adapter
name raises; a not-yet-implemented adapter raises a clear ``NotImplementedError``.

REVERSIBILITY is a PROPERTY of the adapter, honestly declared: ``supports_rollback`` is True only when
the target can redeploy a previous version (ACA revisions can; the mock can). The host executor
classifies a deploy ``reversible`` iff its adapter says so, and ``irreversible_external`` otherwise.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable

# A command runner: takes argv (WITHOUT the leading program), returns stdout. Injectable so unit
# tests never touch ``az`` or the network. Raises subprocess.CalledProcessError on nonzero.
CommandRunner = Callable[..., str]


@dataclass(frozen=True)
class DeployOutcome:
    """The result of a single adapter deploy: the new live revision + reachable URL."""
    ok: bool
    revision: str = ""            # the adapter-native revision id (ACA revision name / mock rev)
    url: str = ""                 # the reachable URL of the deployed revision
    deployed_ref: str = ""        # the exact image/SHA the adapter actually put live
    previous_revision: str = ""   # the revision that was live BEFORE this deploy (for rollback)
    reason: str = ""              # human-readable detail (failure cause, or a success note)
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RollbackOutcome:
    """The result of a host-side rollback to a previous revision."""
    ok: bool
    revision: str = ""            # the revision that is live AFTER the rollback
    url: str = ""
    reason: str = ""
    detail: dict = field(default_factory=dict)


class DeployAdapter:
    """The pluggable deploy interface. A concrete adapter deploys an exact ref to a target and, when
    it ``supports_rollback``, can redeploy a previously-live revision.

    Subclasses implement ``current_revision`` / ``deploy`` / ``rollback``. The host executor is the
    only caller; adapters never see a guest request directly and never handle a credential."""

    name = "base"
    supports_rollback = False
    #: True iff this adapter deploys a container IMAGE (so the host MUST supply a canonical,
    #: digest-pinned, allowlisted image it resolved from the git SHA — the guest never names it).
    #: FAIL-CLOSED DEFAULT: assume an adapter deploys real bytes and needs host image resolution.
    #: A non-image adapter (the mock; a future static-file push) must EXPLICITLY opt out with
    #: ``requires_image_ref = False`` — so a future real image adapter that forgets to set it does
    #: NOT silently skip resolution and deploy an unresolved ref.
    requires_image_ref = True

    def current_revision(self, *, target: str) -> str:
        """The currently-live revision id for ``target`` (``""`` when none / unknown). Recorded by the
        executor BEFORE a deploy so a later rollback knows what to return to."""
        raise NotImplementedError

    def deploy(self, *, target: str, ref: str, sha: str) -> DeployOutcome:
        """Put the exact ``ref`` (pinned by stamped ``sha``) live on ``target``; return the outcome."""
        raise NotImplementedError

    def rollback(self, *, target: str, revision: str) -> RollbackOutcome:
        """Redeploy / re-activate a previously-live ``revision`` on ``target``."""
        raise NotImplementedError


class MockDeployAdapter(DeployAdapter):
    """An in-memory, deterministic adapter that proves the mechanism with NO cloud and NO credential.

    It keeps an ordered revision history per target so a real rollback can return to a prior revision.
    ``supports_rollback`` is True — the mock target genuinely supports redeploying a prior revision —
    so the executor classifies a mock deploy ``reversible`` (the mechanism, honestly)."""

    name = "mock"
    requires_image_ref = False  # explicit opt-out: the mock deploys no real bytes (mock.internal)
    supports_rollback = True

    def __init__(self, *, base_domain: str = "mock.internal") -> None:
        self._base_domain = base_domain
        self._live: dict[str, str] = {}                 # target -> current revision
        self._history: dict[str, list[str]] = {}        # target -> [revisions ever created]
        self._counter: dict[str, int] = {}              # target -> monotonic suffix

    def _rev_name(self, target: str, sha: str) -> str:
        n = self._counter.get(target, 0) + 1
        self._counter[target] = n
        short = sha.replace("sha256:", "")[:8]
        return f"{target}--{short}-{n}"

    def _url(self, target: str) -> str:
        return f"https://{target}.{self._base_domain}"

    def current_revision(self, *, target: str) -> str:
        return self._live.get(target, "")

    def deploy(self, *, target: str, ref: str, sha: str) -> DeployOutcome:
        previous = self._live.get(target, "")
        rev = self._rev_name(target, sha)
        self._history.setdefault(target, []).append(rev)
        self._live[target] = rev
        return DeployOutcome(
            ok=True, revision=rev, url=self._url(target), deployed_ref=ref,
            previous_revision=previous, reason="mock deploy: revision activated",
            detail={"ref": ref, "sha": sha})

    def rollback(self, *, target: str, revision: str) -> RollbackOutcome:
        history = self._history.get(target, [])
        if revision and revision in history:
            self._live[target] = revision
            return RollbackOutcome(
                ok=True, revision=revision, url=self._url(target),
                reason="mock rollback: previous revision re-activated")
        return RollbackOutcome(
            ok=False, revision=self._live.get(target, ""), url=self._url(target),
            reason=f"mock rollback: revision {revision!r} is not a known prior revision of {target!r}")


class AcaDeployAdapter(DeployAdapter):
    """Azure Container Apps adapter via the ``az containerapp`` CLI. HOST-ONLY, real Azure.

    Revision-based: an update creates a new revision (rollback = re-activate the previous one), so
    ``supports_rollback`` is True. The host maps a logical ``target`` to a concrete ``(app, resource
    group)`` — the guest never names an arbitrary Azure resource. ``az`` authenticates from the host's
    OWN login/service principal; this adapter passes NO credential (``env=None`` inherits the host
    login) and never reads one — the credential stays entirely host-side.

    Every ``az`` invocation goes through an injectable :data:`CommandRunner`, so this adapter's command
    construction is unit-tested WITHOUT real Azure. Provisioning a real subscription + ``az login`` is
    the operator's separate live-enablement step (see the build note)."""

    name = "aca"
    supports_rollback = True
    requires_image_ref = True   # ACA deploys an image => the host MUST resolve + pin it (never the guest)

    def __init__(self, *, app: str, resource_group: str, runner: CommandRunner | None = None) -> None:
        if not str(app).strip() or not str(resource_group).strip():
            raise ValueError("AcaDeployAdapter requires a concrete 'app' and 'resource_group'")
        self._app = str(app).strip()
        self._rg = str(resource_group).strip()
        self._run = runner or _default_az

    def _az(self, args: list[str]) -> str:
        # env=None => az inherits the HOST's own login environment; we neither read nor forward any
        # credential. No credential-shaped token is ever placed on the az argv.
        return self._run(["az", *args])

    def current_revision(self, *, target: str) -> str:
        out = self._az([
            "containerapp", "revision", "list", "-n", self._app, "-g", self._rg,
            "--query", "[?properties.active].name | [0]", "-o", "tsv",
        ])
        return (out or "").strip()

    def deploy(self, *, target: str, ref: str, sha: str) -> DeployOutcome:
        previous = self.current_revision(target=target)
        suffix = _revision_suffix(sha)
        # `az containerapp update --image <ref@sha> --revision-suffix <sha8>` creates a NEW revision
        # pinned to the exact ref; it does not mutate a previous one, so rollback stays possible.
        self._az([
            "containerapp", "update", "-n", self._app, "-g", self._rg,
            "--image", ref, "--revision-suffix", suffix,
        ])
        revision = self.current_revision(target=target)
        fqdn = self._az([
            "containerapp", "show", "-n", self._app, "-g", self._rg,
            "--query", "properties.configuration.ingress.fqdn", "-o", "tsv",
        ]).strip()
        url = f"https://{fqdn}" if fqdn else ""
        return DeployOutcome(
            ok=True, revision=revision, url=url, deployed_ref=ref, previous_revision=previous,
            reason="aca deploy: new revision created", detail={"suffix": suffix})

    def rollback(self, *, target: str, revision: str) -> RollbackOutcome:
        if not revision:
            return RollbackOutcome(ok=False, reason="aca rollback: no previous revision recorded")
        self._az([
            "containerapp", "revision", "activate", "-n", self._app, "-g", self._rg,
            "--revision", revision,
        ])
        fqdn = self._az([
            "containerapp", "show", "-n", self._app, "-g", self._rg,
            "--query", "properties.configuration.ingress.fqdn", "-o", "tsv",
        ]).strip()
        return RollbackOutcome(
            ok=True, revision=revision, url=f"https://{fqdn}" if fqdn else "",
            reason="aca rollback: previous revision re-activated")


class StaticHostDeployAdapter(DeployAdapter):
    """Placeholder for a future static-site target (git-push-to-Pages / static host CLI). DEFERRED:
    the interface slot exists so the pluggability is real and tested today, but no static push ships
    in W1a — it fails LOUD rather than silently pretending to deploy."""

    name = "static_host"
    supports_rollback = True  # git-history / immutable-upload targets can redeploy a prior SHA
    requires_image_ref = False  # SHA-only static-file push (no container image); opt out consciously

    def deploy(self, *, target: str, ref: str, sha: str) -> DeployOutcome:  # pragma: no cover
        raise NotImplementedError(
            "static_host adapter is a documented W1a follow-up; only 'mock' (tested) and 'aca' "
            "(real, injected-runner tested) are implemented")


def _revision_suffix(sha: str) -> str:
    """A short, DNS-label-safe revision suffix derived from the stamped SHA (ACA revision suffixes
    must be lowercase alphanumeric/dash). Deterministic, so the same SHA yields the same suffix."""
    s = str(sha).replace("sha256:", "").strip().lower()
    return "".join(c for c in s if c.isalnum())[:10] or "rev"


def _default_az(args: list[str], *, timeout: float = 300.0) -> str:
    proc = subprocess.run(  # noqa: S603 - host-only, args are code-constructed (no guest string)
        args, check=True, timeout=timeout, capture_output=True, text=True,
    )
    return proc.stdout


def get_adapter(name: str, **config: Any) -> DeployAdapter:
    """Construct the adapter named ``name`` with host-provided ``config``. FAIL-CLOSED: an unknown
    name raises ``ValueError`` (never silently a no-op / a different adapter)."""
    key = str(name).strip().lower()
    if key == MockDeployAdapter.name:
        return MockDeployAdapter(**{k: v for k, v in config.items() if k == "base_domain"})
    if key == AcaDeployAdapter.name:
        return AcaDeployAdapter(
            app=config.get("app", ""), resource_group=config.get("resource_group", ""),
            runner=config.get("runner"))
    if key == StaticHostDeployAdapter.name:
        return StaticHostDeployAdapter()
    raise ValueError(f"unknown deploy adapter {name!r}; known: ['aca', 'mock', 'static_host']")
