"""The curated, guest-EMITTED deploy REQUEST — W1a gated deploy pathway (task #6087).

Doctrine (mirrors self-replication #4407/#4834 host-executes / guest-never):
- The autonomous actor may PROPOSE a deploy: "build + deploy git SHA X to the configured target T."
  It writes a typed REQUEST naming ONLY the git SHA (+ minimal build context); it NEVER holds the
  hosting credential, NEVER runs the deploy, and — CRITICALLY — NEVER names the image bytes. It writes
  a request; the HOST-only deploy executor resolves the SHA to its OWN canonical image and executes.

WHY THE REQUEST NAMES A GIT SHA, NOT AN IMAGE (the de-correlated-review HIGH fix):
An earlier version let the request carry a free-form ``image`` string and bound exact-SHA by a mere
SUBSTRING match (``requested_sha in image``). That let a guest pass
``attacker.example.com/backdoor:release-<builtSHA>`` — it validated clean, ``requested==built``
passed, and the HOST deployed the ATTACKER image while the receipt claimed the legit SHA. A
class-level ``deploy: open`` grant approves the CLASS, never a specific image, so within one approved
deploy the guest chose the bytes. So the request now names a git SHA ONLY: a guest-supplied ``image``
(or any other artifact key) is REJECTED here, and the HOST — not the guest — decides which image bytes
that SHA is, resolving it from its own build receipt / registry allowlist + recorded content digest
(``host_deploy_stack``). No guest string ever reaches the deployed image.

WHY THIS MODULE IS GUEST-REACHABLE (and what that forces):
This module is PURE — no subprocess, no az/Azure client, no network, no host effect — so it is safe
to reach from guest code (a future ``/api/deploy/propose`` endpoint would import it to shape-validate
a proposal, exactly like the mgmt API imports the replication request validator). It must therefore
NEVER be the thing that DEPLOYS from data alone, and it must NEVER import the host-only deploy executor
or the adapter that shells out to ``az``. The import firewall enforces that structurally
(``tests/test_deploy_import_firewall.py``): the host deploy step is outside the guest import closure,
so a guest can only ever WRITE a request row — the deploy happens host-side or not at all.

EXACT-SHA, not "latest": the request names an EXACT stamped git SHA (7-64 hex). A floating tag
("latest", a branch name) is REJECTED here, and the host RE-checks the requested SHA against the SHA
actually built before it resolves + deploys (fail-closed).

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
from dataclasses import dataclass
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

#: The ONLY artifact keys a guest may name: the git SHA to build+deploy, and (optionally) a minimal,
#: relative build context path. An ``image`` key — or ANY other key — is REJECTED: the guest never
#: names the image bytes (the host resolves the SHA to its own canonical image).
_ALLOWED_ARTIFACT_KEYS: frozenset[str] = frozenset({"git_sha", "build_context"})

#: An EXACT stamped git SHA: hex, >=7 (the short-SHA floor) and <=64. A tag / branch / "latest" does
#: NOT match — that is the whole point (deploy a specific version, never a moving ref).
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
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
#: A build context is a short RELATIVE path inside the build tree; never absolute, never a traversal.
_MAX_CONTEXT_LEN = 256


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True iff the deploy bridge is switched on. Default OFF => the bridge is inert."""
    source = env if env is not None else os.environ
    return str(source.get(FLAG, "")).strip().lower() in {"1", "true", "yes", "on"}


def is_exact_sha(ref: Any) -> bool:
    """True iff ``ref`` is an EXACT stamped git SHA (7-64 hex), not a floating tag."""
    if not isinstance(ref, str):
        return False
    r = ref.strip()
    if r.lower() in _FLOATING_REFS:
        return False
    return bool(_SHA_RE.match(r))


def normalize_sha(ref: Any) -> str:
    """Lowercased, whitespace-trimmed git SHA for equality comparison. Empty for a non-SHA input (so
    two non-SHAs never compare equal by accident)."""
    return str(ref).strip().lower() if is_exact_sha(ref) else ""


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
    and only through the gate, and it resolves the image itself. Names a git SHA, never image bytes.
    Shaped toward the V2 WorkContract/receipt JSON (BB #2716)."""
    target: str                     # the logical, host-configured target (e.g. the ACA app label)
    adapter: str                    # which adapter deploys it (mock | aca | static_host)
    requested_sha: str              # the EXACT stamped git SHA to build+deploy (never a tag)
    artifact: Mapping[str, Any]     # {"git_sha": <sha>, "build_context": <relative path>} ONLY
    requester_instance_id: str
    mission_id: str
    note: str = ""
    guest_executes: bool = False    # MUST be False — the host executes; asserted by the validator
    policy_version: str = "deploy-request-v2"  # v2: git-SHA-only artifact (host resolves the image)

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


def _validate_artifact(artifact: Any, requested_sha: Any, errors: list[str]) -> None:
    """The artifact names ONLY a git SHA (== requested_sha) + an optional relative build context. An
    ``image`` key, any unknown key, an absolute/traversal context, or a git_sha != requested_sha is
    rejected — the guest can never name the image bytes or deploy a SHA it did not build."""
    if not isinstance(artifact, Mapping) or not artifact:
        errors.append("artifact must be a mapping: {'git_sha': <sha>, 'build_context': <relative path>}")
        return

    unknown = set(map(str, artifact.keys())) - _ALLOWED_ARTIFACT_KEYS
    if "image" in unknown:
        errors.append(
            "artifact must not carry an 'image' ref: a guest never names the image bytes; the HOST "
            "resolves the git SHA to its own canonical, digest-pinned, registry-allowlisted image")
    for extra in sorted(unknown - {"image"}):
        errors.append(f"artifact has unknown key {extra!r}; only {sorted(_ALLOWED_ARTIFACT_KEYS)} allowed")

    git_sha = artifact.get("git_sha")
    if not is_exact_sha(git_sha):
        errors.append(f"artifact.git_sha must be an EXACT git SHA (7-64 hex), got {git_sha!r}")
    elif is_exact_sha(requested_sha) and normalize_sha(git_sha) != normalize_sha(requested_sha):
        errors.append("artifact.git_sha must EQUAL requested_sha (build and deploy the SAME stamped SHA)")

    ctx = artifact.get("build_context")
    if ctx is not None:
        if not isinstance(ctx, str) or len(ctx) > _MAX_CONTEXT_LEN:
            errors.append("artifact.build_context must be a short string path")
        elif ctx.strip().startswith("/") or ".." in ctx.replace("\\", "/").split("/"):
            errors.append("artifact.build_context must be a RELATIVE path with no '..' traversal")


def validate_deploy_request(packet: Any) -> list[str]:
    """Deny-by-default SHAPE + safety validation of a guest deploy request. Empty list == accepted.

    Enforced here (guest-reachable, so purely structural — the AUTHORITY gate, the built-vs-request
    SHA check, and the host-canonical image resolution are HOST-side, re-derived, never trusting this
    stored packet):
      * known adapter, non-empty target, identified mission + requester;
      * EXACT-SHA: ``requested_sha`` is a stamped git SHA, never a floating tag ("latest");
      * artifact names ONLY a git SHA (== requested_sha) + an optional relative build context — NO
        ``image`` ref (the guest never names the image bytes);
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
            f"requested_sha must be an EXACT stamped git SHA (7-64 hex), never a floating tag like "
            f"'latest'; got {requested_sha!r}")

    _validate_artifact(packet.get("artifact"), requested_sha, errors)

    if packet.get("guest_executes") is True:
        errors.append("guest_executes must not be true — the HOST executes the deploy, never the guest")

    return errors


def build_deploy_request(
    *, target: str, adapter: str, git_sha: str, build_context: str = ".",
    requester_instance_id: str, mission_id: str, note: str = "",
) -> DeployRequest:
    """Construct a validated :class:`DeployRequest` naming a git SHA (never an image). Raises
    ``ValueError`` on any validation error so a malformed proposal fails LOUD at construction."""
    req = DeployRequest(
        target=str(target), adapter=str(adapter), requested_sha=str(git_sha),
        artifact={"git_sha": str(git_sha), "build_context": str(build_context)},
        requester_instance_id=str(requester_instance_id), mission_id=str(mission_id), note=str(note))
    errors = validate_deploy_request(req.to_packet())
    if errors:
        raise ValueError("invalid deploy request: " + "; ".join(errors))
    return req
