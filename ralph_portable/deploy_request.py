"""The curated, guest-EMITTED deploy REQUEST — W1a gated deploy pathway (task #6087).

Doctrine (mirrors self-replication #4407/#4834 host-executes / guest-never):
- The autonomous actor may PROPOSE a deploy: "push built artifact X (an image ref, or a git SHA +
  build context) to the configured target T, at EXACTLY this stamped SHA." It writes a typed
  REQUEST; it NEVER holds the hosting credential and NEVER runs the deploy itself. The HOST
  validates the stored request and executes it (the host-only deploy executor), the same way
  the guest only ever PROPOSES a replication row and the host broker stands the stack up.

WHY THIS MODULE IS GUEST-REACHABLE (and what that forces):
This module is PURE — no subprocess, no az/Azure client, no network, no host effect — so it is safe
to reach from guest code (a future ``/api/deploy/propose`` endpoint would import it to shape-validate
a proposal, exactly like the mgmt API imports the replication request validator). It must therefore
NEVER be the thing that DEPLOYS from data alone, and it must NEVER import the host deploy executor or
the adapter that shells out to ``az``. The import firewall enforces that structurally
(``tests/test_deploy_import_firewall.py``): the host deploy step is outside the guest import closure,
so a guest can only ever WRITE a request row — the deploy happens host-side or not at all.

EXACT-SHA, not "latest": the request names an EXACT stamped SHA (a 7-64 hex git SHA, or a
``sha256:...`` image digest). A floating tag ("latest", a branch name) is REJECTED here, and the
host RE-checks the requested SHA against the SHA actually built before it deploys (fail-closed). This
is the deploy-hygiene invariant (what is deployed is a specific stamped version, never a moving ref).

NO CREDENTIAL EVER RIDES THE REQUEST: a guest request that smuggles a credential/secret/token key is
rejected here (defense-in-depth). The real credential is host-only — absent from the act sandbox env
(``runner.executor._AGENT_ENV_NAMES`` is a deny-by-default allowlist that lists no deploy credential)
and guest-forbidden by the replication modification allowlist (a ``*CREDENTIAL*``/``*SECRET*`` key
trips ``replication_modifications.forbidden_key_reason``), so a replica can never hand one to a child.

Flag-gated by ``AQ_DEPLOY_BRIDGE``; default OFF => the bridge is inert (the host executor refuses to
act and, since nothing existing imports this module, a flag-off checkout is byte-identical).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

#: Environment flag. Default OFF => the whole deploy bridge is inert.
FLAG = "AQ_DEPLOY_BRIDGE"

#: Recognized deploy adapters (selectable by the request, resolved host-side). ``mock`` proves the
#: mechanism with no cloud; ``aca`` is Azure Container Apps (revision-based rollback); ``static_host``
#: is a documented follow-up slot so a static-site target can be added without touching this contract.
ADAPTER_MOCK = "mock"
ADAPTER_ACA = "aca"
ADAPTER_STATIC_HOST = "static_host"
KNOWN_ADAPTERS: frozenset[str] = frozenset({ADAPTER_MOCK, ADAPTER_ACA, ADAPTER_STATIC_HOST})

#: An EXACT stamped SHA: a hex git SHA (>=7, <=64) OR an OCI ``sha256:<64hex>`` image digest. A tag /
#: branch / "latest" does NOT match — that is the whole point (deploy a specific version, never a
#: moving ref).
_SHA_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{7,64}$")
#: Floating refs an actor might try to pass instead of a stamped SHA. Named for a specific rejection.
_FLOATING_REFS = frozenset({"latest", "main", "master", "head", "stable", "edge", "prod", "current"})

#: Credential/secret markers a guest request must NEVER carry (mirrors the modification allowlist's
#: forbidden substrings). The deploy credential is HOST-only; nothing about it rides the request.
_CREDENTIAL_MARKERS: tuple[str, ...] = (
    "PASSWORD", "SECRET", "TOKEN", "CREDENTIAL", "AZURE_CLIENT", "SP_", "SERVICE_PRINCIPAL",
    "AQ_DEPLOY_AZURE", "SUBSCRIPTION_KEY", "ACCESS_KEY", "API_KEY", "PRIVATE_KEY",
)

#: The maximum length of any scalar field — a request is small typed metadata, not a payload.
_MAX_FIELD_LEN = 1024


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True iff the deploy bridge is switched on. Default OFF => the bridge is inert."""
    source = env if env is not None else os.environ
    return str(source.get(FLAG, "")).strip().lower() in {"1", "true", "yes", "on"}


def is_exact_sha(ref: Any) -> bool:
    """True iff ``ref`` is an EXACT stamped SHA (git hex or ``sha256:`` digest), not a floating tag."""
    if not isinstance(ref, str):
        return False
    r = ref.strip()
    if r.lower() in _FLOATING_REFS:
        return False
    return bool(_SHA_RE.match(r))


def normalize_sha(ref: Any) -> str:
    """Lowercased, ``sha256:``-stripped, whitespace-trimmed SHA for equality comparison. Empty for a
    non-SHA input (so two non-SHAs never compare equal by accident)."""
    if not is_exact_sha(ref):
        return ""
    r = str(ref).strip().lower()
    return r[len("sha256:"):] if r.startswith("sha256:") else r


def credential_marker(key: Any) -> str | None:
    """Return the credential marker a key name trips, or None. Case-insensitive substring match."""
    up = str(key).strip().upper()
    for marker in _CREDENTIAL_MARKERS:
        if marker in up:
            return marker
    return None


@dataclass(frozen=True)
class DeployRequest:
    """A typed, curated deploy proposal. Immutable value object carrying ZERO authority: building or
    storing one neither deploys nor opens a gate — the HOST executor is the only thing that deploys,
    and only through the gate. Shaped toward the V2 WorkContract/receipt JSON (BB #2716)."""
    target: str                     # the logical, host-configured target (e.g. the ACA app label)
    adapter: str                    # which adapter deploys it (mock | aca | static_host)
    requested_sha: str              # the EXACT stamped SHA/digest to deploy (never a tag)
    artifact: Mapping[str, Any]     # {"image": <ref>} OR {"git_sha": <sha>, "build_context": <path>}
    requester_instance_id: str
    mission_id: str
    note: str = ""
    guest_executes: bool = False    # MUST be False — the host executes; asserted by the validator
    policy_version: str = "deploy-request-v1"

    def to_packet(self) -> dict[str, Any]:
        """The JSON packet a guest stores / a host reads."""
        return {
            "target": self.target,
            "adapter": self.adapter,
            "requested_sha": self.requested_sha,
            "artifact": dict(self.artifact),
            "requester_instance_id": self.requester_instance_id,
            "mission_id": self.mission_id,
            "note": self.note,
            "guest_executes": self.guest_executes,
            "policy_version": self.policy_version,
        }


def _artifact_references_sha(artifact: Mapping[str, Any], requested_sha: str) -> bool:
    """The artifact must actually carry the requested SHA — so a request can't build one SHA and ask
    to deploy another. For a git-context build ``git_sha`` must EQUAL ``requested_sha``; for an image
    the image ref must be pinned by that SHA (its ``sha256:`` digest, or the hex as a tag/suffix)."""
    want = normalize_sha(requested_sha)
    if not want:
        return False
    git_sha = artifact.get("git_sha")
    if git_sha is not None:
        return normalize_sha(git_sha) == want
    image = artifact.get("image")
    if isinstance(image, str):
        return want in image.strip().lower()
    return False


def validate_deploy_request(packet: Any) -> list[str]:
    """Deny-by-default SHAPE + safety validation of a guest deploy request. Empty list == accepted.

    Enforced here (guest-reachable, so purely structural — the AUTHORITY gate and the built-vs-request
    SHA check are HOST-side, re-derived, never trusting this stored packet):
      * known adapter, non-empty target, identified mission + requester;
      * EXACT-SHA: ``requested_sha`` is a stamped SHA/digest, never a floating tag ("latest");
      * artifact is an ``image`` ref OR a ``git_sha`` + build context, and it CARRIES the requested SHA;
      * the guest never claims it will execute the deploy itself (``guest_executes`` must be false);
      * NO credential/secret/token key rides the request (defense-in-depth).
    """
    if not isinstance(packet, Mapping):
        return ["deploy_request must be a mapping"]

    errors: list[str] = []

    # No credential ever rides a guest request (checked FIRST so a smuggled secret is always caught,
    # even on an otherwise-malformed packet).
    for key in packet:
        marker = credential_marker(key)
        if marker is not None:
            errors.append(
                f"deploy_request must not carry a credential/secret key {key!r} (matches {marker!r}); "
                "the deploy credential is host-only and never rides the request")

    adapter = str(packet.get("adapter") or "").strip()
    if adapter not in KNOWN_ADAPTERS:
        errors.append(f"adapter must be one of {sorted(KNOWN_ADAPTERS)}, got {packet.get('adapter')!r}")

    target = str(packet.get("target") or "").strip()
    if not target:
        errors.append("target is required (the host-configured deploy target)")
    elif len(target) > _MAX_FIELD_LEN:
        errors.append("target is too long")

    if not str(packet.get("mission_id") or packet.get("mission") or "").strip():
        errors.append("mission or mission_id is required (identified mission for the deploy)")
    if not str(packet.get("requester_instance_id") or "").strip():
        errors.append("requester_instance_id is required")

    requested_sha = packet.get("requested_sha")
    if not is_exact_sha(requested_sha):
        errors.append(
            f"requested_sha must be an EXACT stamped SHA (7-64 hex, or sha256:<digest>), never a "
            f"floating tag like 'latest'; got {requested_sha!r}")

    artifact = packet.get("artifact")
    if not isinstance(artifact, Mapping) or not artifact:
        errors.append("artifact must be a mapping: {'image': <ref>} or {'git_sha': <sha>, "
                      "'build_context': <path>}")
    else:
        has_image = isinstance(artifact.get("image"), str) and artifact.get("image").strip()
        has_git = artifact.get("git_sha") is not None
        if not has_image and not has_git:
            errors.append("artifact must name either an 'image' ref or a 'git_sha' (+ build context)")
        if has_git and not is_exact_sha(artifact.get("git_sha")):
            errors.append(f"artifact.git_sha must be an exact SHA, got {artifact.get('git_sha')!r}")
        # Only assert the reference-consistency once the requested SHA itself is well-formed, so the
        # error list points at the ROOT problem (a bad SHA) rather than a confusing derived one.
        if is_exact_sha(requested_sha) and (has_image or has_git) and \
                not _artifact_references_sha(artifact, str(requested_sha)):
            errors.append(
                "artifact does not carry requested_sha: the built artifact and the SHA to deploy must "
                "be the SAME stamped version (a request can't build one SHA and deploy another)")

    if packet.get("guest_executes") is True:
        errors.append("guest_executes must not be true — the HOST executes the deploy, never the guest")

    return errors


def build_deploy_request(
    *, target: str, adapter: str, requested_sha: str, artifact: Mapping[str, Any],
    requester_instance_id: str, mission_id: str, note: str = "",
) -> DeployRequest:
    """Construct a validated :class:`DeployRequest`. Raises ``ValueError`` on any validation error so
    a malformed proposal fails LOUD at construction, never silently produces a bad packet."""
    req = DeployRequest(
        target=str(target), adapter=str(adapter), requested_sha=str(requested_sha),
        artifact=dict(artifact), requester_instance_id=str(requester_instance_id),
        mission_id=str(mission_id), note=str(note))
    errors = validate_deploy_request(req.to_packet())
    if errors:
        raise ValueError("invalid deploy request: " + "; ".join(errors))
    return req
