# Agent-comms Phase 2 — replica outbox → parent experiment journal

Builds on Phase 0 (`COMMS_PHASE0.md`) and Phase 1 (`COMMS_PHASE1.md`). Design doc:
`docs/design/agent-comms-a2a-design.md`, §10 Phase 2 + §4 (envelope/stores) + §8 (authority, esp.
§8.3 the authorization matrix).

Phase 2 lets a **replica AUTHOR** bounded status/experiment envelopes (**untrusted claims**), the
**host relay** them into the **parent journal**, and the **parent** own a **separate verification
state**. There is **no parent→replica command path** (that is Phase 3), and — the load-bearing
invariant — **a replica result can never change any parent authority state.** Adoption is a later,
separately-gated action, not in Phase 2. Nothing here grants a capability.

## What landed

### 1. Replica-authored payload schemas — `management/api/comms_payloads.py` (guest-safe)
Versioned, size-capped payloads for the three replica-emit kinds:
- `status.report` `{schema_version, text, state?}`;
- `experiment.progress` `{schema_version, text, progress?∈[0,1], correlation_id?}`;
- `experiment.result` `{schema_version, summary, outcome_claimed, verification_required:true,
  artifact_refs[], metrics{}}`.

**Artifact references are immutable digests + metadata ONLY** — `digest` must be
`sha256:<64-hex>`, and the allowed key set is exactly `{digest, media_type, size_bytes, name}`. Any
`path` / `url` / `uri` / `location` / `fetch` key, or a `media_type`/`name` that looks like a URL or
path, is **rejected** (the SSRF / path-traversal boundary, design §4.1/§8.5). `outcome_claimed` is a
**claim**, never a verdict; `artifact_digest_matches(ref, content)` is the check the parent runs
against the real artifact bytes (a mismatch is flagged/rejected).

### 2. Authenticated outbox publish + scoped outbox pull — `management/api/app.py`, `comms_auth.py`
- `POST /api/agent-comms` (Phase 0) now **hardens** the three replica kinds through the per-kind
  schema. A replica **always** gets `trust=untrusted_claim` — it can never mint `host_observed`.
- `GET /api/agent-comms/outbox?after_seq=&limit=` — **read-only** outbox pull for the host relay,
  behind a **separate scoped credential** (`can_read_outbox`, `AQ_COMMS_OUTBOX_READ_TOKEN`). A plain
  publish-only replica credential is **denied** (403); unauthenticated is 401. Returns a monotonic
  `seq` cursor.
- `store.envelopes_after(after_seq, limit)` (both stores) — the cursor read.

### 3. Host outbox relay — `scripts/host_outbox_relay.py` (HOST-ONLY, SSRF-safe)
Reuses the Phase-1 poller discipline. For each **live** registry replica it pulls
`http://127.0.0.1:<registry app_mgmt port>/api/agent-comms/outbox` and **copies** each envelope into
the **parent journal**:
- **SSRF-safe.** The only URL it builds comes from `registry.live()` ports; it **refuses to follow
  any 3xx redirect** (`_NoRedirectHandler`) and re-asserts loopback host + exact
  `/api/agent-comms/outbox` path before trusting a body. It is a read-only GET; it **never POSTs to a
  replica** (no parent→replica path).
- **Idempotent.** Copies dedup on the **immutable global envelope id** (`store.relay_envelope` →
  in-memory id set / Pg `ON CONFLICT (id) DO NOTHING`), and a **per-replica monotonic cursor** in the
  host registry (`record_relay_cursor` / `relay_cursor`) lets a restart resume without re-pulling.
- **Original + relay timestamps.** Each copy keeps `trust=untrusted_claim` (a relay can **never**
  upgrade trust), keeps the original `id`/`created_at`, sets `delivery=relayed` (a transport state,
  never evidence of execution), and adds a `relay{relayed_at, relayed_by, source_instance_id,
  original_created_at}` block.
- **Dropped/expired.** A claim whose `expires_at` has passed is **dropped** (never copied) while the
  cursor still advances past it (`is_expired`).
- **Teardown mid-transfer.** Each copy is its own idempotent op; a torn-down replica is skipped
  (not live) so no orphan/partial copy results, and a re-pull re-relays safely.
- **No token ⇒ fail-closed skip:** a replica with no resolvable relay token is skipped, never scanned.

### 4. Parent-owned verification state — `store.py`, `app.py`, `comms_results.py`
- A **separate** `ralph_comms_verifications` table (in-memory dict mirror), keyed by envelope id:
  `state ∈ {unverified, verified, rejected}`, `verifier`, `reason`, `evidence`, `verified_at`. It
  **never mutates** the immutable claim envelope.
- `POST /api/agent-comms/{id}/verify` — **operator-only** (401/403 otherwise). A replica has **no
  path** to it. Setting it is **not** adoption/merge/promotion — it only records whether the parent
  independently checked the claim.
- `GET /api/agent-comms/results` — operator view: replica claims projected with `claim:true`, the
  trust class, and the verification state, **defaulting to `unverified`**.

### 5. Fail-safe, flag-gated loop emit — `runner/comms_emit.py`, `runner/loop.py`
`runner/comms_emit.py` (guest-safe) publishes a bounded `status.report` to the replica's **own** local
outbox after a completed cycle. The loop calls `self._emit_comms_lifecycle(cyc)` next to the cycle
heartbeat. It obeys the causal-consult / self-correction / heartbeat discipline:
- **default OFF.** Unless `AQ_COMMS_EMIT` is truthy, it is a complete no-op **before any work** — the
  live default loop is **byte-identical**;
- **fail-safe.** Every entry point swallows all exceptions (and the loop hook swallows again) — a
  comms-emit failure can **never** crash, block, or alter the loop or any decision/side effect;
- **no authority.** It publishes an untrusted claim and stops. It is the **only** loop touch and adds
  **no** decision-path change. The import firewall confirms `runner/loop.py` and `runner/comms_emit.py`
  closures never reach the host-owned registry / relay / poller.

### 6. UI trust + verification badges — `management/frontend/`
- `CommsPanel` badges `untrusted_claim` as **“replica claim”** vs **“host-observed.”**
- New `ResultsPanel` (`/api/agent-comms/results`, operator token): each replica result is shown as an
  **untrusted claim** with its parent verification badge (unverified/verified/rejected), artifact
  **digests** (never clickable paths/URLs), and **verify / reject** actions. Recording a verdict is
  explicitly **not** adoption.

## The load-bearing safety invariant (attacked by the de-correlated review)
A replica-authored `experiment.result` — even forged, claiming success — **must not change any parent
authority state:** not mission truth/measure, not learning promotion, not a merge, not adoption, not a
governance principle, not fleet health. Proven in `tests/test_comms_invariant.py`: a forged success
result lands `untrusted_claim` + `unverified` + badged as a claim; **fleet health is unchanged**
(host_observed only — a replica cannot mint it even when granted the `health.observed` kind); **no
merge/replication/adoption** is triggered; nothing auto-verifies it; a digest mismatch is detected
against ground truth and **rejected**; and even an operator `verified` verdict is **not** adoption.

## Safety invariants held (carried from Phase 0/1)
- **Server-derived identity** — claimed identity fields are rejected (`extra=forbid`); a replica can
  never spoof another principal or mint `host_observed`.
- **ACL deny-by-default** — a replica publishes only to its own lineage channels; outbox read needs
  the scoped relay/operator credential.
- **Host relay SSRF-safe** — refuse redirects, registry ports only, read-only outbox route, host-only
  import firewall.
- **Fail-closed durable writes** — a configured-but-unavailable DB 503s; the relay never falls back
  to memory.
- **Artifact refs are digests only** — no host path / auto-fetch URL.
- **Loop emit** — fail-safe + flag-gated + default-off (byte-identical live loop). No capability
  granted; no parent→replica command path.

## Tests (design §10 Phase 2)
- `tests/test_comms_payloads.py` — versioning + size caps; **artifact refs reject host paths/URLs**;
  sha256 digest format; digest **match/mismatch** check; extra-scalar guard.
- `tests/test_comms_outbox_api.py` — outbox scope (401/403/allow), **cursor pagination**; publish
  hardening (forged reference 400); verify operator-only; results default unverified.
- `tests/test_comms_relay.py` — **at-least-once + duplicate suppression** (global-id idempotent),
  **cursor restart resume**, monotonic cursor, **teardown mid-transfer (no orphan)**,
  **dropped/expired**, no-token fail-closed, **SSRF guards** (reject non-loopback/non-outbox/non-http,
  refuse redirect), relay stamps original+relay timestamps and keeps trust untrusted.
- `tests/test_comms_store_phase2.py` — `envelopes_after` cursor, id-idempotent `relay_envelope`,
  verification separate from the claim.
- `tests/test_comms_loop_emit.py` — **flag-off byte-identical (no network)**, **fail-safe** (API/
  payload failure never raises), on-path emits a correct status.report, loop hook swallows an emit bug.
- `tests/test_comms_invariant.py` — **THE INVARIANT** (forged result changes no authority state);
  replica cannot mint host_observed; **digest mismatch flagged/rejected**; verified ≠ adoption.
- `tests/test_comms_two_stack_e2e.py` — **two-stack** relay across **separate stores** over the
  replica's real `/outbox` HTTP (always-on portable ground truth); the heavy real-Docker two-stack
  variant is **double-gated + skips loudly** (docker + >45% free memory; `AQ_COMMS_DOCKER_LIVE=1`).

`pytest -q` is green (heavy/docker paths gated + skipping loudly).

## Run
```bash
# Host relay (host-only): copy each live replica's outbox into the parent journal, once.
python scripts/host_outbox_relay.py --once
# Enable the replica loop emit (default OFF — the live default loop is byte-identical until set):
export AQ_COMMS_EMIT=1
```
The host presents a scoped outbox-read token per replica via `AQ_COMMS_RELAY_TOKENS`
(`{instance_id: token}`) or a single `AQ_COMMS_OUTBOX_READ_TOKEN`; the replica recognizes it as a
read-only, publish-nothing credential.
