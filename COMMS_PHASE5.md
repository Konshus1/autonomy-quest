# Agent-comms Phase 5 — conforming-shaped A2A 1.0 façade + optional tbagents bridge (LANDED INERT)

**Status:** landed inert on `task/4834-comms-phase5`. The `/a2a` surface is flag-gated **default-OFF**;
the tbagents bridge is host-side **default-OFF**; the live loop/deployment are untouched and **not
redeployed**. Kevin is surfaced before any live enablement. This completes the agent-comms phased plan
(Phases 0–5), per `docs/design/agent-comms-a2a-design.md` §7 / §8.6 / §10 Phase 5 / §11.

---

## What this phase is (and is not)

Phase 5 adds a **separate, translation-only A2A 1.0-shaped façade** at `/a2a`, plus a `/.well-known/
agent-card.json` Agent Card, that **translates to the SAME existing typed comms queues** built in
Phases 0–4. It is **not** a second source of truth and **not** a new authority path.

- `/api/agent-comms` (the AQ-native mailbox/operator API) is **unchanged**.
- `/a2a` is a **third ingestion point** into the same typed queues, and it **re-applies the SAME
  untrust treatment** as the native endpoints: server-derived identity, ACL, per-kind payload
  validation, the kind allowlist, and the artifact-digest-only rule. It never trusts the A2A envelope.

### New files
| File | Role |
| --- | --- |
| `management/api/a2a_translate.py` | Pure translation core: Agent Card, version negotiation, A2A Message↔AQ payload mapping, malicious-part rejection, honest Task-state projection. |
| `management/api/a2a_router.py` | The flag-gated JSON-RPC router (`message/send`, `tasks/get`, `tasks/list`, `tasks/cancel`) + `a2a_enabled()` gate + `build_a2a_router()` / `build_a2a_app()`. |
| `scripts/host_tbagents_bridge.py` | Optional host-only, one-way, redacted, default-off operator-view bridge into tbagents. |
| `management/api/app.py` (edit) | Mounts `/a2a` + the card **only** when `AQ_A2A_ENABLE` is truthy. |

### Mapping (design §10 Phase 5)
- **experiment work → A2A Tasks.** A stored envelope is projected (never a new record) to an A2A Task
  via `envelope_to_task`. `task_id` = envelope id; the owner = the envelope's server-derived
  `principal_id`.
- **updates → Messages / TaskStatus.** Task `status.state` is derived honestly from the envelope's
  kind + inert/verification state.
- **outputs → Artifacts.** `experiment.result` artifact refs project to A2A Artifacts whose parts carry
  **digest + metadata only** — never a `uri`/`url`/`path` (symmetric with the inbound rule).

### Skills advertised (only implemented ones — design §7)
`aq.report-status` → `status.report` · `aq.submit-experiment-result` → `experiment.result` ·
`aq.request-work` → `work.request` (inert inbox). **Deliberately absent:** `replicate-host`,
`run-shell`, `change-capability`, `approve`, `adopt-result`. There is **no generic command/shell skill**.
Streaming and push notifications are **not advertised** (not implemented), so there is no streaming
disconnect/recovery surface to get wrong.

---

## Conformance: what IS and IS NOT verified (honesty over false interoperability, §7)

**No official A2A SDK or TCK is available offline** in this repo's deliberately minimal dependency
model (`requirements.txt`). Per §7, we therefore implemented the **conforming A2A 1.0 wire contract
directly** (JSON-RPC 2.0 binding + a minimal Agent Card modelled on the published A2A 1.0 object
model) rather than vendoring a heavy/unavailable dependency.

- **Verified (this repo):** the Agent Card shape, JSON-RPC request/response + A2A error codes, version
  negotiation, auth, per-principal task scoping, idempotent replay/cancel, malicious-part rejection,
  and the authority invariant — all by `tests/test_a2a_facade.py` + `tests/test_a2a_invariant.py`.
- **NOT verified:** interoperability against the **official A2A inspector/TCK** (not run — not
  available offline).

**We therefore do NOT claim "A2A compliant."** The Agent Card states its own status:
`aq:conformance = "a2a-1.0-shaped; interop NOT verified by official inspector/TCK"`, and
`test_a2a_facade.py::test_honest_conformance_claim_in_card` asserts the card never carries a bare
"A2A compliant" claim. To claim compliance later, run the official inspector/TCK and gate the claim on
its result.

---

## THE LOAD-BEARING INVARIANT — the A2A protocol is NOT authority (§8.6 / §11)

Passing an A2A TCK would prove **interoperability**, never **AQ authority safety**. Nothing arriving
over `/a2a` — an authenticated client, an `AUTH_REQUIRED`-style handshake, a Task reaching
`completed`/`canceled`, a conformant card, a Message/Artifact — can satisfy, skip, weaken, or
pre-approve **any** AQ gate: not mission truth, learning promotion, merge, adoption, the replication
cap/memory/auto-approve gates, the parent-owned verification state, or
`requires_human`/approval/blast-radius/budget.

- An inbound A2A `aq.request-work` Task is **untrusted input** that lands in the **same inert typed
  inbox** as a Phase-3 `work.request` (server-derived origin, `trust=untrusted_claim`,
  `delivery=delivered`), subject to **all** the same downstream gates. The importer is separate and
  **default-off**; even when explicitly enabled it drops every actuation field (goal-only allowlist)
  and the consequence gate forces `requires_human=True` at max blast radius.
- A `completed` Task is a **transport state**, never evidence of measured success or AQ adoption. Every
  projected Task carries `aq:authority = "none"` and an explicit disclaimer note; a claim's
  `verificationState` stays `unverified` until the parent independently verifies.

Proven by `tests/test_a2a_invariant.py` (each asserts AQ authority state is byte-unchanged after an
authenticated A2A client does its worst).

---

## Safety properties carried (design §8)

- **Default-off `/a2a`.** `AQ_A2A_ENABLE` unset ⇒ routes **not mounted** (404), no new surface on a
  default checkout or the live deployment (`tests/test_a2a_default_off.py`).
- **Server-derived identity.** Identity comes from the credential, never the A2A body; a client cannot
  spoof another principal, the host, or the operator.
- **Per-principal task scoping.** A principal sees/acts on **only** its own tasks; cross-principal
  `tasks/get`/`cancel` returns `TaskNotFound` (no existence oracle).
- **Deny-by-default ACL.** Publish reuses `authorize_publish`; inbound work reuses
  `authorize_inbox_deliver` (only the true parent lineage, right target).
- **Artifact-digest-only.** No SSRF via A2A Artifact URLs: file parts with `uri`/`url` are refused,
  inline `bytes` are refused, and artifact refs are validated by the existing digest-only validator
  (forbidden `path`/`url`/`uri`/`location`/`fetch` keys rejected). Oversize/too-many parts rejected.
- **Fail-closed.** A configured-but-unavailable durable store never yields an ephemeral ack.
- **tbagents bridge:** redacted + one-way + host-only + default-off (below).
- **Import firewall holds.** The A2A façade is guest-safe (imports no host-only code); the bridge is
  host-only (`tests/test_import_firewall.py`).

---

## Safety-review hardening (de-correlated protocol/security review — no blocking findings)

Three hardening findings were fixed before landing (final phase):

1. **Structural anti-spoofing (teeth), not by-omission.** The `/a2a` JSON-RPC handler parses a raw
   dict and was previously safe only because it *didn't read* body-supplied identity — unlike the
   native endpoints' structural `extra="forbid"`. `reject_claimed_identity()` now rejects any inbound
   object carrying `principal_id`/`principalId`/`origin_instance_id`/`originInstanceId`/`trust`/
   `aq:trust` (or a bare `host_observed` value) with `-32602`, **before any handler runs**, closing the
   exact Phase-2/Phase-3 spoof-by-body regression class. Negative regression tests
   (`test_a2a_invariant.py`) assert spoofed identity/trust in metadata **and** data parts is rejected
   with nothing stored, while a clean request still stores server-derived + `untrusted_claim`.
   Red-first verified: with the guard removed **and** a handler that honors body `aq:trust` (mutation
   M2), a spoof stores `trust=host_observed` (test RED); restoring the guard neutralizes M2 (GREEN).
2. **Broader bridge redaction.** `scrub_text` now redacts any IPv4`[:port]` (not just localhost),
   generic `key=value` secrets (`pw=`/`password=`/`secret=`/`token=`/`key=`/`dsn=`…), AWS-key shapes,
   and a lower opaque-token threshold; sha256 digests are still preserved. Tests cover the reviewer's
   exact leak strings.
3. **Private public card.** The unauthenticated `/.well-known/agent-card.json` no longer echoes
   `AQ_INSTANCE_ID`.

## Optional tbagents bridge (`scripts/host_tbagents_bridge.py`)

An **inert operator-view tool** (design §5 Option E) — not wired to any daemon/entrypoint.
- **Default-off:** needs both `AQ_TBAGENTS_BRIDGE` truthy **and** `INTERNAL_SERVICE_TOKEN`; a dry run
  never contacts tbagents.
- **One-way:** reads the parent journal and POSTs a redacted mirror into tbagents; **no AQ write-back**
  (structurally: the module exposes no AQ store/queue writer).
- **Redacted:** drops secret/token/credential/DSN keys and host-local ports; scrubs URLs, host:port,
  absolute paths, and bearer/opaque tokens from free text; **preserves sha256 digests**. Mirrors only
  operator-view channels/kinds — the command plane (`work.request`/`response`/`receipt`, the inbox) is
  never mirrored.
- **Host-only:** imports the host-owned `fleet_registry`, so the import firewall keeps it out of the
  guest closure.

---

## Enablement (operator, when surfaced)

Nothing here is on. To enable the façade on a chosen instance: set `AQ_A2A_ENABLE=1` and restart the
management API (mounts `/a2a` + the card). To run the bridge: set `AQ_TBAGENTS_BRIDGE=1` +
`INTERNAL_SERVICE_TOKEN` and invoke the script (start with `--dry-run`). Both are gated on an explicit
operator decision; the live loop stays byte-identical and is not redeployed by this phase.

## Tests
`tests/test_a2a_facade.py` · `tests/test_a2a_invariant.py` · `tests/test_a2a_default_off.py` ·
`tests/test_a2a_tbagents_bridge.py` · `tests/test_import_firewall.py` (extended). Full suite:
`pytest -q` green (heavy/DB/network paths skip loudly; the official TCK path is not run and is not
asserted).
