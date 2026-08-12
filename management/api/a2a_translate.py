"""A2A 1.0 translation layer: A2A wire objects <-> AQ typed comms envelopes (#4834 comms Phase 5,
design §7 / §8.6 / §10 Phase 5).

This is the guest-safe, pure-translation core of the SEPARATE ``/a2a`` façade. It is NOT a second
source of truth and NOT a new authority path: it TRANSLATES A2A Messages/Tasks/Artifacts into the
SAME existing typed queues the AQ-native ``/api/agent-comms`` writes, re-applying the SAME untrust
treatment (kind allowlist, payload validation, artifact-digest-only). It imports NOTHING host-only
and grants NO capability.

HONEST CONFORMANCE (design §7): no official A2A SDK / TCK is available offline in this repo's
dependency model, so this implements the conforming A2A 1.0 *wire contract* (JSON-RPC 2.0 binding +
a minimal Agent Card) DIRECTLY. It is SHAPE-verified against the published A2A object model by the
tests in this repo; it is NOT verified by the official A2A inspector/TCK. We therefore do NOT claim
"A2A compliant" — we claim "A2A-1.0-shaped, translation-only, interop-unverified". See COMMS_PHASE5.md.

THE LOAD-BEARING INVARIANT (design §8.6 / §11): the A2A protocol is NOT authority. Nothing here — a
conformant card, an authenticated client, a Message/Artifact, a Task reaching a terminal state —
satisfies, skips, weakens, or pre-approves any AQ gate. An inbound A2A ``aq.request-work`` Task lands
in the SAME inert typed inbox as a Phase-3 ``work.request``, subject to every downstream gate; a
"completed" Task is a TRANSPORT state, never evidence of a measured success or an AQ adoption. Every
projected Task carries explicit ``aq:*`` metadata saying exactly this.
"""

from __future__ import annotations

from typing import Any

from management.api.comms_envelope import (
    MAX_ID_LEN,
    MAX_TEXT_BYTES,
    _payload_size,
)
from management.api.comms_payloads import (
    _validate_artifact_ref,
    validate_replica_payload,
)
from management.api.comms_workrequest import validate_inbound_payload

# ---------------------------------------------------------------------------
# Protocol identity + version negotiation (design §7: "version negotiation").
# ---------------------------------------------------------------------------
# The A2A protocol versions this façade implements the wire contract for. We advertise exactly what
# we implement — no aspirational versions (the same honesty rule as the skills list below).
A2A_PROTOCOL_VERSION = "1.0"
SUPPORTED_A2A_VERSIONS = ("1.0",)

# ---------------------------------------------------------------------------
# JSON-RPC 2.0 + A2A error codes (from the published A2A 1.0 spec error registry).
# ---------------------------------------------------------------------------
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

A2A_TASK_NOT_FOUND = -32001
A2A_TASK_NOT_CANCELABLE = -32002
A2A_PUSH_NOT_SUPPORTED = -32003
A2A_UNSUPPORTED_OPERATION = -32004
A2A_CONTENT_TYPE_NOT_SUPPORTED = -32005
# Local extension code for version negotiation failure (kept in the JSON-RPC server-error band).
A2A_UNSUPPORTED_VERSION = -32000

# ---------------------------------------------------------------------------
# Input hardening bounds (design §8.5). Strict maxima so one A2A request cannot exhaust storage.
# ---------------------------------------------------------------------------
MAX_PARTS = 64
MAX_ARTIFACTS_IN = 32
MAX_DATA_PART_BYTES = 32 * 1024


class A2AError(Exception):
    """A translation/validation failure carrying a JSON-RPC error ``code`` + ``message``.

    The router maps this straight to a JSON-RPC error object. ``code`` is one of the constants above.
    """

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# The skill registry: ONLY implemented skills, each pinned to an allowlisted AQ kind + a route.
#
# route="publish" -> translates to a replica-authored untrusted CLAIM on the local outbox (the SAME
#                    path as native POST /api/agent-comms), gated by ``authorize_publish``.
# route="inbox"   -> translates to an INERT inbox work.request (the SAME path as native
#                    POST /api/agent-comms/inbox), gated by ``authorize_inbox_deliver`` and left inert
#                    for the separate, default-off importer + every downstream gate.
#
# DELIBERATELY ABSENT (design §7): replicate-host, run-shell, change-capability, approve, adopt-result.
# There is NO generic command/shell skill — a skill is a callable INTERFACE, never an entitlement.
# ---------------------------------------------------------------------------
A2A_SKILLS: dict[str, dict[str, Any]] = {
    "aq.report-status": {"kind": "status.report", "route": "publish"},
    "aq.submit-experiment-result": {"kind": "experiment.result", "route": "publish"},
    "aq.request-work": {"kind": "work.request", "route": "inbox"},
}


def _skill_card_entry(skill_id: str, name: str, description: str, example: str) -> dict[str, Any]:
    return {
        "id": skill_id,
        "name": name,
        "description": description,
        "tags": ["autonomy-quest", "aq", A2A_SKILLS[skill_id]["kind"]],
        "examples": [example],
        # We accept text + structured data parts; we never accept a file part with a fetchable URI
        # (SSRF boundary) — outputs are digest references only. Declared honestly here.
        "inputModes": ["text/plain", "application/json"],
        "outputModes": ["application/json"],
    }


def build_agent_card(*, base_url: str, instance_id: str | None = None) -> dict[str, Any]:
    """A MINIMAL A2A Agent Card advertising ONLY implemented skills/capabilities (design §7).

    ``capabilities.streaming`` and ``pushNotifications`` are FALSE — we do not implement them, so we
    do not advertise them (the honesty rule: no aspirational capability). ``securitySchemes`` declares
    the bearer credential the façade actually requires; a skill entry describes a callable interface,
    NOT an entitlement (design §8.6).
    """
    card: dict[str, Any] = {
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "name": "Autonomy Quest (AQ) A2A façade",
        "description": (
            "A conforming-shaped, translation-only A2A 1.0 façade over AQ's existing typed comms "
            "queues. Skills are callable interfaces, not entitlements: an inbound Task is untrusted "
            "input landing in an inert typed queue, subject to every AQ gate. Protocol is not "
            "authority."
        ),
        "url": base_url,
        "preferredTransport": "JSONRPC",
        "version": "0.1.0",
        "capabilities": {
            # Advertise only what is implemented. We do NOT advertise streaming, so there is no
            # streaming disconnect/recovery surface to get wrong (design §10 Phase 5).
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json"],
        "securitySchemes": {
            "aqCommsBearer": {
                "type": "http",
                "scheme": "bearer",
                "description": (
                    "The AQ per-principal comms credential. Identity is SERVER-DERIVED from it; a "
                    "client cannot spoof another principal, the host, or the operator."
                ),
            }
        },
        "security": [{"aqCommsBearer": []}],
        "skills": [
            _skill_card_entry(
                "aq.report-status", "Report status",
                "Report a bounded status.report as an untrusted claim to this instance's outbox.",
                "Send a status.report message: {\"kind\":\"text\",\"text\":\"cycle 12 healthy\"}."),
            _skill_card_entry(
                "aq.submit-experiment-result", "Submit experiment result",
                "Submit an experiment.result as an untrusted claim; outputs are digest-only artifact "
                "references. It never adopts/merges/promotes — the parent verifies independently.",
                "Send a result with a data part carrying {\"artifact_ref\":{\"digest\":\"sha256:...\"}}."),
            _skill_card_entry(
                "aq.request-work", "Request bounded work",
                "Submit a bounded work.request. It lands in an INERT typed inbox as untrusted input, "
                "subject to every local budget/blast-radius/approval gate; it actuates nothing.",
                "Send a work.request: {\"kind\":\"text\",\"text\":\"summarize experiment E7\"}."),
        ],
        # Non-standard but explicit: the AQ authority disclaimer travels ON the card so any A2A client
        # (or inspector) sees it. Metadata is a permitted extension point.
        "metadata": {
            "aq:authority": "none",
            "aq:note": (
                "This A2A surface is a translation façade. No A2A operation — auth handshake, a Task "
                "reaching a terminal state, a conformant card — satisfies any AQ gate (mission, "
                "learning promotion, merge, adoption, replication cap/memory/auto-approve, "
                "verification, requires_human/approval/blast-radius/budget)."
            ),
            "aq:supportedProtocolVersions": list(SUPPORTED_A2A_VERSIONS),
            "aq:conformance": "a2a-1.0-shaped; interop NOT verified by official inspector/TCK",
        },
    }
    if instance_id:
        card["metadata"]["aq:instanceId"] = instance_id
    return card


def negotiate_version(requested: str | None) -> str:
    """Return the protocol version to use, or raise ``A2AError`` on an unsupported request.

    A caller may pin a version via the ``X-A2A-Version`` header or ``params.protocolVersion``. Absent
    => default to the advertised version (latest supported). Present-but-unsupported => fail closed
    with a clear error rather than silently pretending compatibility (design §7 version negotiation).
    """
    if requested is None or requested == "":
        return A2A_PROTOCOL_VERSION
    if not isinstance(requested, str):
        raise A2AError(JSONRPC_INVALID_PARAMS, "protocolVersion must be a string")
    if requested not in SUPPORTED_A2A_VERSIONS:
        raise A2AError(
            A2A_UNSUPPORTED_VERSION,
            f"unsupported A2A protocol version {requested!r}; supported: {list(SUPPORTED_A2A_VERSIONS)}")
    return requested


# ---------------------------------------------------------------------------
# Inbound: A2A Message -> AQ payload (per skill), re-applying the SAME untrust treatment.
# ---------------------------------------------------------------------------
def _text_of(parts: list[Any]) -> str:
    """Concatenate all TextPart text, bounded. Injection boundary: this becomes untrusted payload
    data (goal/summary/text), never concatenated into any system/developer instruction."""
    chunks: list[str] = []
    total = 0
    for p in parts:
        if isinstance(p, dict) and p.get("kind") == "text":
            t = p.get("text")
            if t is None:
                continue
            if not isinstance(t, str):
                raise A2AError(JSONRPC_INVALID_PARAMS, "a text part's 'text' must be a string")
            total += len(t.encode("utf-8"))
            if total > MAX_TEXT_BYTES:
                raise A2AError(JSONRPC_INVALID_PARAMS, f"combined text exceeds {MAX_TEXT_BYTES} bytes")
            chunks.append(t)
    return "\n".join(chunks)


def _reject_file_parts(parts: list[Any]) -> None:
    """SSRF + digest-only boundary (design §4.1 / §8.5): a FilePart carrying a fetchable ``uri`` is
    REJECTED (no auto-fetch URL) and a FilePart carrying inline ``bytes`` is REJECTED (the comms plane
    does not ingest raw bytes — artifact evidence is a digest reference only). So artifact evidence
    MUST arrive as a DataPart ``{"artifact_ref": {"digest": "sha256:..."}}``, never a file locator."""
    for p in parts:
        if isinstance(p, dict) and p.get("kind") == "file":
            f = p.get("file") or {}
            if isinstance(f, dict) and ("uri" in f or "url" in f):
                raise A2AError(
                    A2A_CONTENT_TYPE_NOT_SUPPORTED,
                    "file parts with a uri/url are refused (no auto-fetch; SSRF boundary). Send an "
                    "artifact as a data part with a sha256 digest reference.")
            raise A2AError(
                A2A_CONTENT_TYPE_NOT_SUPPORTED,
                "inline file-byte parts are not accepted on the comms plane; reference an artifact "
                "by its sha256 digest in a data part instead (artifact-digest-only rule).")


def _data_parts(parts: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in parts:
        if isinstance(p, dict) and p.get("kind") == "data":
            d = p.get("data")
            if not isinstance(d, dict):
                raise A2AError(JSONRPC_INVALID_PARAMS, "a data part's 'data' must be an object")
            if _payload_size(d) > MAX_DATA_PART_BYTES:
                raise A2AError(JSONRPC_INVALID_PARAMS,
                               f"a data part exceeds {MAX_DATA_PART_BYTES} bytes")
            out.append(d)
    return out


def _collect_artifact_refs(data_parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull artifact references from data parts and validate each with the EXISTING digest-only
    validator (which rejects any path/url/uri/location/fetch key). Accepts either a single
    ``{"artifact_ref": {...}}`` or ``{"artifact_refs": [...]}`` per data part."""
    raw: list[Any] = []
    for d in data_parts:
        if "artifact_ref" in d:
            raw.append(d["artifact_ref"])
        if "artifact_refs" in d:
            refs = d["artifact_refs"]
            if not isinstance(refs, list):
                raise A2AError(JSONRPC_INVALID_PARAMS, "artifact_refs must be a list")
            raw.extend(refs)
    if len(raw) > MAX_ARTIFACTS_IN:
        raise A2AError(JSONRPC_INVALID_PARAMS, f"more than {MAX_ARTIFACTS_IN} artifact references")
    # _validate_artifact_ref raises PayloadError (an EnvelopeError) on a forbidden key/bad digest.
    from management.api.comms_payloads import PayloadError

    validated: list[dict[str, Any]] = []
    for i, r in enumerate(raw):
        try:
            validated.append(_validate_artifact_ref(r, index=i))
        except PayloadError as exc:
            raise A2AError(JSONRPC_INVALID_PARAMS, str(exc))
    return validated


def _validate_parts_envelope(message: Any) -> list[Any]:
    if not isinstance(message, dict):
        raise A2AError(JSONRPC_INVALID_PARAMS, "params.message must be an object")
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        raise A2AError(JSONRPC_INVALID_PARAMS, "params.message.parts must be a non-empty list")
    if len(parts) > MAX_PARTS:
        raise A2AError(JSONRPC_INVALID_PARAMS, f"too many parts (> {MAX_PARTS})")
    return parts


def resolve_skill(params: dict[str, Any], message: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Resolve the target skill id from the request (deny-by-default: an unknown/absent skill fails).

    The skill is named via ``message.metadata.skillId`` (A2A metadata is an allowed extension point),
    with ``params.metadata.skillId`` / ``params.metadata['aq:skill']`` as fallbacks. It MUST be one of
    the advertised skills — a client cannot invoke an unadvertised or generic operation."""
    candidates = []
    for meta in (message.get("metadata"), params.get("metadata")):
        if isinstance(meta, dict):
            candidates.append(meta.get("skillId"))
            candidates.append(meta.get("aq:skill"))
    skill_id = next((c for c in candidates if isinstance(c, str) and c), None)
    if not skill_id:
        raise A2AError(
            JSONRPC_INVALID_PARAMS,
            "message.metadata.skillId is required and must name an advertised skill "
            f"({sorted(A2A_SKILLS)})")
    spec = A2A_SKILLS.get(skill_id)
    if spec is None:
        raise A2AError(
            JSONRPC_INVALID_PARAMS,
            f"skill {skill_id!r} is not advertised; advertised skills: {sorted(A2A_SKILLS)}")
    return skill_id, spec


def message_to_payload(skill_id: str, spec: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    """Translate a validated A2A message into the AQ per-kind payload for ``spec['kind']``, re-running
    the SAME per-kind validators the native endpoints use (guard-at-every-door discipline).

    Rejects malicious parts (file uri/bytes -> SSRF/oversize; oversize data; forbidden artifact keys).
    """
    parts = _validate_parts_envelope(message)
    _reject_file_parts(parts)
    text = _text_of(parts)
    data_parts = _data_parts(parts)
    kind = spec["kind"]

    from management.api.comms_envelope import EnvelopeError

    try:
        if kind == "status.report":
            if not text.strip():
                raise A2AError(JSONRPC_INVALID_PARAMS, "aq.report-status requires text")
            return validate_replica_payload("status.report", {"text": text})
        if kind == "experiment.result":
            refs = _collect_artifact_refs(data_parts)
            metrics: dict[str, Any] = {}
            outcome = "unknown"
            for d in data_parts:
                if isinstance(d.get("metrics"), dict):
                    metrics.update(d["metrics"])
                if isinstance(d.get("outcome_claimed"), str):
                    outcome = d["outcome_claimed"]
            if not text.strip():
                raise A2AError(JSONRPC_INVALID_PARAMS, "aq.submit-experiment-result requires a summary")
            return validate_replica_payload("experiment.result", {
                "summary": text, "outcome_claimed": outcome,
                "artifact_refs": refs, "metrics": metrics})
        if kind == "work.request":
            if not text.strip():
                raise A2AError(JSONRPC_INVALID_PARAMS, "aq.request-work requires a goal (text)")
            # SAME guard #1 the native inbox runs; extra data-part keys are quarantined inert.
            base: dict[str, Any] = {"goal": text}
            for d in data_parts:
                for k, v in d.items():
                    base.setdefault(k, v)
            return validate_inbound_payload("work.request", base)
    except EnvelopeError as exc:
        # PayloadError / WorkRequestError / EnvelopeError all funnel to an invalid-params error.
        raise A2AError(JSONRPC_INVALID_PARAMS, str(exc))
    raise A2AError(JSONRPC_INVALID_PARAMS, f"unroutable skill kind {kind!r}")


# ---------------------------------------------------------------------------
# Outbound: AQ envelope -> A2A Task / Message / Artifact projection.
# ---------------------------------------------------------------------------
# Honest state mapping: an AQ envelope's kind + inert/verification state -> an A2A TaskState. A
# terminal "completed" is a TRANSPORT fact only; the accompanying metadata says so explicitly.
_KIND_TO_STATE = {
    "work.request": "submitted",       # inert inbox item; the default-off importer has not run
    "work.response": "working",
    "receipt": "working",
    "status.report": "working",
    "experiment.progress": "working",
    "experiment.result": "completed",  # the CLAIM was transmitted; NOT adoption/verification
    "health.observed": "working",
    "operator.message": "working",
}


def _artifact_refs_to_a2a(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project stored digest-only artifact refs into A2A Artifacts. Each becomes an Artifact whose
    single DataPart carries the DIGEST + metadata — never a uri/path (so the outbound A2A view also
    holds no fetchable locator; the digest-only rule is symmetric)."""
    out: list[dict[str, Any]] = []
    for i, r in enumerate(refs or []):
        digest = r.get("digest")
        out.append({
            "artifactId": f"{digest}" if isinstance(digest, str) else f"artifact-{i}",
            "name": r.get("name") or "artifact",
            "description": "digest-only reference (no host path / no auto-fetch URL)",
            "parts": [{"kind": "data", "data": {k: r[k] for k in r}}],
        })
    return out


def envelope_to_task(
    envelope: dict[str, Any], *, verification: dict[str, Any] | None = None,
    import_state: dict[str, Any] | None = None, canceled: bool = False,
) -> dict[str, Any]:
    """Project one stored AQ envelope into an A2A Task (a translation VIEW; not a new record).

    ``verification`` / ``import_state`` are the PARENT-OWNED states (never derived from the envelope
    itself). ``canceled`` marks a task a scoped cancel request has targeted. Every task carries
    ``aq:*`` metadata making the authority disclaimer explicit and assertable."""
    kind = envelope.get("kind")
    state = "canceled" if canceled else _KIND_TO_STATE.get(kind, "working")
    payload = envelope.get("payload") or {}
    task_id = envelope.get("id")
    context_id = envelope.get("correlation_id") or envelope.get("channel") or task_id

    artifacts = _artifact_refs_to_a2a(payload.get("artifact_refs") or [])
    verification_state = (verification or {}).get("state", "unverified")
    import_st = (import_state or {}).get("state", "not_imported")

    status_text = {
        "submitted": "Stored as an INERT, untrusted work.request; the default-off importer has not "
                     "run and every local gate remains ahead of any action.",
        "completed": "TRANSPORT state only: the untrusted claim was transmitted. This is NOT AQ "
                     "adoption, verification, or gate satisfaction.",
        "canceled": "A scoped cancellation request was recorded (a gated proposal, never a kill).",
    }.get(state, "Untrusted A2A message translated to an AQ typed-queue envelope.")

    return {
        "kind": "task",
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": state,
            "message": {
                "kind": "message",
                "role": "agent",
                "messageId": f"status-{task_id}",
                "parts": [{"kind": "text", "text": status_text}],
            },
            "timestamp": envelope.get("created_at"),
        },
        "artifacts": artifacts,
        "history": [],
        "metadata": {
            "aq:kind": kind,
            "aq:trust": envelope.get("trust"),
            "aq:authority": "none",
            "aq:deliveryState": envelope.get("delivery"),
            "aq:verificationState": verification_state,
            "aq:importState": import_st,
            "aq:originInstanceId": envelope.get("origin_instance_id"),
            "aq:principalId": envelope.get("principal_id"),
            "aq:note": (
                "A2A transport/task state is not AQ authority. This envelope grants no capability and "
                "actuates nothing; adoption/verification/merge/gates are unchanged."
            ),
        },
    }


def _bounded_id(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise A2AError(JSONRPC_INVALID_PARAMS, f"{field} must be a string")
    if len(value) > MAX_ID_LEN:
        raise A2AError(JSONRPC_INVALID_PARAMS, f"{field} exceeds {MAX_ID_LEN} chars")
    return value
