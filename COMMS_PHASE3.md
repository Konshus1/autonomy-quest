# Agent-comms Phase 3 — parent/operator → replica typed WORK REQUESTS (lands INERT)

Builds on Phase 0 (`COMMS_PHASE0.md`), Phase 1 (`COMMS_PHASE1.md`), Phase 2 (`COMMS_PHASE2.md`).
Design doc: `docs/design/agent-comms-a2a-design.md`, §10 Phase 3 + §4 (envelope) + §8 (authority/
security, esp. §8.3 matrix and §8.4 safe command intake).

Phase 3 is the **FIRST parent→replica command direction**. It **lands INERT**: the full stored-inbox +
importer + gates + reply/ack + UI are built, but **the importer is flag-gated DEFAULT-OFF**, **the
host→replica POST relay is a standalone tool not wired to any daemon**, and **nothing is enabled on the
live loop**. A default checkout — and the live loop — never acts on a single `work.request`. Kevin is
surfaced before any live enablement.

**The load-bearing invariant.** No parent/operator message can, on the replica: invoke Docker or any
host/replication actuation; mutate replication controls (`AQ_REPLICATION_*` / cap / memory / auto-
approve / daemon-enable); grant credentials or capabilities; bypass or pre-satisfy the local budget /
blast-radius / approval / permission gates; be counted as measured success / mission truth / a
learning-promotion input; or trigger "replicate" / "run shell" / "merge" / "promote" by injection. A
`work.request` is untrusted input that — **only when the importer is explicitly enabled** — becomes an
ordinary **gated proposal** in the local planning queue, nothing more.

**The Phase-2 lesson carried forward:** a trust-boundary guard holds at **every** ingestion point, not
just the front door. Phase 3 has **two** ingestion points — the replica **inbox endpoint** and the
**importer** — and **both** treat the `work.request` as fully untrusted input.

## What landed

### 1. work.request schema + the IMPORT FIELD ALLOWLIST — `management/api/comms_workrequest.py` (guest-safe)
Versioned, size-capped payloads for the parent↔replica command kinds:
- `work.request` `{schema_version, goal, priority?∈{low,normal,high}, constraints?, cancel_of?}`;
- `work.response` `{schema_version, state∈{accepted_as_proposal,queued_for_approval,declined,cancel_requested}, …}`;
- `receipt` `{schema_version, disposition∈{received,stored,imported,rejected,expired,duplicate}, …}`.

There is deliberately **no** generic `command` / `shell` / executable kind (design §8.4). The
**deny-by-default import allowlist** is `IMPORTABLE_FIELDS = {goal, priority, constraints, cancel_of}`.
Two guard functions:
- `validate_inbound_payload(kind, payload)` — **ingestion guard #1** (arrival): shape/size, a bounded
  `goal` required for `work.request`; **extra caller keys are quarantined** under `_untrusted_extra` as
  inert display data, **never promoted** to the top level.
- `select_importable_fields(payload)` — **ingestion guard #2** (import): reads **ONLY** the allowlist
  from the top level and **drops everything else** (including anything in `_untrusted_extra`).
  `assert set(out) <= IMPORTABLE_FIELDS` is a structural proof the importer can only ever emit
  allowlisted fields.
- `work_import_enabled(env)` — `AQ_COMMS_WORK_IMPORT`, **default OFF**; only an explicit truthy value
  flips it on.

### 2. Replica INERT INBOX — `POST /api/agent-comms/inbox` in `management/api/app.py`
Authenticated, ACL'd, fail-closed, idempotent, expiry-checked storage of an inbound work message.
Storage grants **no** capability and triggers **no** action:
1. **Authenticate** the scoped inbox credential and **derive** the sender principal (the parent) from
   it — a body cannot forge identity (`extra=forbid` → 422 on any claimed `origin_instance_id`).
2. **Receiver ACL** (`comms_auth.authorize_inbox_deliver`, deny-by-default): the principal must carry
   the scoped inbox-deliver capability; the kind must be an inbound work kind; **wrong-target** (a
   request addressed to a sibling) → 403; **wrong-parent** (server-derived sender ≠ this replica's
   configured parent) → 403. Only the true parent lineage may `work.request` this replica.
3. **Re-validate** the payload (guard #1) → 400 on malformed/oversize/no-goal.
4. **Expiry**: an already-expired request → 409, never stored.
5. **Fail closed** on a configured-but-unavailable durable store → 503; **idempotent** on the
   idempotency key. The stored envelope is **always** `trust=untrusted_claim`, server-minted id +
   server-derived identity, `delivery=delivered`. Response is explicit: `stored_inert:true`,
   `imported:false`.

`GET /api/agent-comms/inbox` (operator-only) projects inbound work badged as untrusted proposals with
their parent-owned import state (`management/api/comms_inbox.py`, read-only).

### 3. SEPARATE FLAG-GATED importer — `management/api/comms_import.py` (DEFAULT-OFF)
`import_pending_work_requests(...)` runs **only** when `AQ_COMMS_WORK_IMPORT` is enabled. With the flag
off it returns `{"enabled": False, "imported": 0, …}` and touches nothing — no `create_work`, no record
(the default-off, byte-identical guarantee). When enabled, for each stored `work.request`:
- apply the field allowlist (guard #2) → `{goal, priority?, constraints?, cancel_of?}`;
- build a plan **with NO expense/blast facts** (they are not importable) and run the **real**
  `runner.consequence_gate.assess_plan_gate`. Absent blast facts ⇒ `assess_blast_radius` returns level
  3 (honest absence ⇒ most-restrictive) ⇒ `requires_human=True`. **The request can never pre-satisfy,
  skip, or weaken the gate**; a claimed low-blast / `auto_approve` / `requires_human:false` is dropped
  before the gate ever runs;
- enqueue via the injected local queue's `create_work` (the same surface the mission loop uses) with
  `requires_human` **from the gate** — it never actuates, replicates, grants a capability, runs a
  shell, mutates `AQ_REPLICATION_*`, or records a measured success;
- record a parent-owned import record keyed on the envelope id (`store.set_import` / `ralph_comms_imports`)
  so **re-import is idempotent** and a **crash between store and import leaves no partial actuation**.

The importer imports nothing host-only and is **not** called by `runner/loop.py` (the loop stays
byte-identical).

### 4. reply/ack + cancel-as-request
`work.response` / `receipt` are validated (`comms_payloads.validate_replica_payload`) and carried back
to the parent by the existing Phase-2 outbox relay (`EMITTABLE_KINDS = REPLICA_EMIT_KINDS ∪
{work.response, receipt}`). **Cancellation is a request**, not a process signal: a `work.request` with
`cancel_of=<correlation_id>` imports (when enabled) to a `imported_work_cancel_request` proposal that
**still requires human approval** — never a kill/signal.

### 5. Host→replica work.request relay — `scripts/host_workrequest_relay.py` (HOST-ONLY, SSRF-safe, INERT TOOL)
The reverse direction of Phase 1/2 (a POST, not a GET), with the **same rigor**:
- **target port ONLY from `registry.live()`** — never a body/proposal/caller host; the only URL built
  is `http://127.0.0.1:<registry app_mgmt port>/api/agent-comms/inbox`;
- **loopback-only + exact route**, re-asserted before and after the request (`_assert_loopback_inbox_url`);
- **refuses redirects** (`_NoRedirectHandler`);
- **scoped host credential** (`AQ_COMMS_INBOX_TOKEN` / `AQ_COMMS_INBOX_RELAY_TOKENS`), distinct from the
  outbox-read token; no token ⇒ no POST (fail-closed); unknown/torn-down instance ⇒ refused;
- **bounded** timeout + hard request-body cap; the body is a clean schema-validated `work.request`.
It is **host-only** (imports `fleet_registry`, forbidden to guests) and **not wired to any daemon** —
the CLI even refuses to run without `--yes`.

### 6. Credential wiring (inert)
`comms_auth.from_env` adds the scoped inbox-deliver credential (`AQ_COMMS_INBOX_TOKEN` +
`AQ_COMMS_PARENT_INSTANCE_ID` → a principal that **is** the parent). `host_replica_stack` generates a
distinct `AQ_COMMS_INBOX_TOKEN` per replica at stand-up (bound host-side by fingerprint). This is inert
wiring: no daemon POSTs it, no importer consumes it by default.

## Safety properties (evidence)
- **work-request-cannot-actuate / replicate / shell / bypass-gate / be-measured-success** —
  `tests/test_comms_phase3_invariant.py`, `tests/test_comms_importer.py`.
- **field-allowlist-drops-forbidden** — `test_allowlist_drops_every_actuation_field`,
  `test_enabled_import_drops_every_actuation_field`.
- **all-local-gates-still-run-when-imported** — `test_enabled_import_is_gated_and_requires_human`,
  `test_a_low_blast_claim_cannot_skip_approval` (the real `assess_plan_gate` runs; requires-human,
  blast level 3, policy version stamped).
- **default-off-byte-identical** — `test_import_flag_is_default_off`, `test_default_off_imports_nothing`,
  `test_default_off_is_byte_identical_no_import`; `runner/loop.py`, `runner/executor.py`, `runner/db.py`,
  container entrypoint, and `docker-compose.yml` are untouched.
- **SSRF host-POST safe** — `tests/test_comms_workrequest_relay.py` (registry-only port, loopback, no
  redirect, exact route, no body host, fail-closed token, bounded body).
- **wrong-parent / wrong-target rejected; replay/expiry/malformed/oversize; fail-closed** —
  `tests/test_comms_inbox_api.py`.
- **import-firewall** — `tests/test_import_firewall.py`: the relay is host-only (reaches the registry,
  absent from the guest closure); the guest-side Phase-3 modules are registry-free.

## Defense-in-depth hardening (post safety review — no blocking hole; these harden the findings)
- **Finding 1 — a third store-entry path (the real ingestion-boundary gap):** `POST /api/agent-comms`
  (publish) does not run guard #1 on a `work.request` (it is not in `EMITTABLE_KINDS`), so a replica's
  own credential could store a **self-authored** `work.request` keeping forbidden top-level fields. Fix:
  the importer now imports **only** requests that arrived via the parent path — `delivery=="delivered"`
  **and** `origin_instance_id == the configured parent` (`AQ_COMMS_PARENT_INSTANCE_ID`); a self-authored
  / non-delivered / wrong-origin request is **skipped** with a reason, even with the importer enabled.
  This is a **second independent guard** on top of the field allowlist, not a replacement.
  (`tests/test_comms_importer.py`, `tests/test_comms_inbox_api.py::test_self_authored_publish_work_request_is_not_imported`.)
- **Finding 2 — keep the parent credential off the guest:** (a) `authorize_inbox_deliver` now refuses a
  credential whose derived origin == the receiving replica's own instance id (a replica can never be its
  own parent — self-delivery refused); (b) the parent-scoped `AQ_COMMS_INBOX_TOKEN` is **no longer
  written into the replica's guest-readable secret file** — provisioning it is a separate operator-
  surfaced live-enablement step, so until then the inbox endpoint has no configured credential and
  refuses delivery (401), the correct land-inert posture.
  (`tests/test_comms_inbox_api.py::test_self_delivery_is_rejected`.)
- **Finding 3 — the importer clamps its own gate:** `map_work_request_to_proposal` clamps
  `blast_gate_level` to `<=3` internally and asserts the imported item is always `requires_human=True`,
  so a caller passing a higher level can never silently weaken the gate.
- **Finding 4 — input hardening:** a pre-parse `Content-Length` cap (64KB middleware) on the inbox
  route, and `_payload_size` now rejects deeply-nested payloads (`MAX_PAYLOAD_DEPTH=32`, and catches
  `RecursionError`) instead of 500-ing.

## Land-inert contract
- Importer **default-OFF** (`AQ_COMMS_WORK_IMPORT` unset).
- Host→replica relay is a standalone tool, **not wired** to any running daemon/loop.
- Live loop **untouched / byte-identical**. Do **not** enable auto-import. Kevin is surfaced before any
  live enablement.

`pytest -q`: green (heavy/docker tests skipped/gated).
