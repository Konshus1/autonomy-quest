# Self-Correction Consultant — Stage 2: the ARBITER (LIVE apply)

Task #4834, stage 2 of a two-stage make-it-live. Stage 1
([`SELF_CORRECTION_SHADOW.md`](SELF_CORRECTION_SHADOW.md)) only **observed** what the pure
`runner/consultants/self_correction.py::consult()` would recommend. Stage 2 adds the **arbiter**:
the one component allowed to **select and apply** at most one bounded, reversible action per cycle —
behind its **own** flag, `AQ_SELF_CORRECTION_LIVE`, **default OFF**.

Landing this changes nothing until a human flips the flag. Off ⇒ the apply is a **complete no-op**
and the loop behaves exactly as stage-1 (shadow-only telemetry, or nothing at all). **Do not flip
the flag here; the LIVE flip is gated on the human operator.**

## The priority order (at most one action per cycle)

Before the executor's DECIDE, the ordered pure-consultant chain runs (today: `self_correction`;
later consultants append). The **pure** `arbiter()` (`self_correction_arbiter.py`) selects **at most
one** action by this fixed priority — the `#4834.details.shared_seam_contract` chain:

| # | Condition (first match wins) | Arbiter outcome | Effect on the loop |
|---|---|---|---|
| 1 | any recommendation with `requires_human` | `ESCALATE` | notify the human; **never auto-apply**. Mission dispatch proceeds. |
| 2 | else a `frame_review` recommendation | `HOLD_DISPATCH` | **hold routine mission dispatch this cycle** (return before DECIDE); recorded. Bounded to one cycle. |
| 3 | else a `sibling_audit` recommendation | `ENQUEUE_AUDIT` | enqueue a bounded, reversible re-examination request per suspect sibling. Mission dispatch proceeds. |
| 4 | else (a `ConsultantPass`, or nothing) | `PROCEED` | normal DECIDE. |

"Human-gated escalates; else frame-review outranks a routine dispatch; else the sibling audit
enqueues; else normal DECIDE." The arbiter is a **pure decision** with no DB/executor handle; the
loop applies it.

## The two apply-actions (both bounded, both reversible)

### `sibling_audit` → enqueue a re-examination REQUEST (never an authorization)

For each **suspect** same-rule sibling (`self_correction.sibling_audit_targets()` — the suspects
the recommendation is built from, never the corrected item itself), the loop appends **one** row to
`self_correction_audit_request`:

```
{principle_id, generating_rule_id, reason, source_correction, source_correction_item_id,
 source_corrected_at, work_context, detail}
```

This is the review's hard constraint made mechanical: **reopen-for-REVIEW, never authorization.**
The row asks the evaluator/governance to *look again* at that principle. It **never** changes a
disposition, **never** promotes/demotes, **never** authorizes. Enqueuing is **idempotent** — a
`UNIQUE (principle_id, source_correction)` key plus `ON CONFLICT DO NOTHING` means the same
correction re-run (e.g. the same cycle repeated) cannot open a duplicate request for the same
sibling. `source_correction` encodes the *specific* correction event (`corrected-item@corrected-at`),
so a genuinely **new** re-correction of the same principle yields a fresh, distinct request.

### `frame_review` → hold routine dispatch for exactly one cycle

The loop **does not dispatch new mission work** this cycle: the hook returns before the DECIDE
executor call, so there is no work row and **no DECIDE model call**. It records *why* to
`self_correction_arbiter_action` (`held_dispatch = true`). The hold carries **no persistent
blocking state** — the next cycle re-evaluates from a fresh snapshot and dispatches normally. No
other side effect.

### `requires_human` → escalate (park/notify), never auto-apply

A recommendation that declares it needs human judgement (an unvalidated sibling, or a corrected item
whose snapshot rule disagrees so siblings can't be safely enumerated) is **never** auto-applied. The
loop records the escalation and notifies the operator; mission work continues (escalate does **not**
hold dispatch — a hold-forever would be a DoS).

## Why the apply can never authorize anything

`aq_loop` — the principal the loop runs as — **cannot write any principle transition**. `schema/999`
revokes `INSERT, UPDATE, DELETE, TRUNCATE` on `causal_principle_transition` from it, and
`schema/027` grants it **nothing** on any transition surface — only `INSERT` on the two append-only
apply tables (plus `USAGE, SELECT` on their sequences), exactly as `schema/026` did for the shadow
log. A promotion/demotion is a transition; the loop physically cannot make one. Verified live:
`has_table_privilege('aq_loop','causal_principle_transition','INSERT'|'UPDATE'|'DELETE')` is all
`false` (`tests/test_self_correction_arbiter_pg.py::test_aq_loop_cannot_write_any_principle_transition`).

## Bounded + reversible (both provable)

- **Bounded**: the arbiter returns exactly one action (unit-proven for every priority level,
  including "two frame_reviews + a sibling_audit still yield one HOLD"); the enqueue writes one row
  per suspect sibling and is idempotent per correction.
- **Reversible**: an audit request is an **inert queue row** — no disposition changes because it
  exists. Ignoring it leaves every principle exactly as it was; the evaluator later consumes it and
  may record a *separate* resolution, never mutating this row (both tables reject `UPDATE`/`DELETE`)
  and never a disposition. A `frame_review` hold is bounded to a single cycle and stores no blocking
  state. Live-verified: the append-only trigger rejects `UPDATE`/`DELETE`, and enqueuing writes
  **zero** principle transitions.

## Fail-safe (never stuck, never a bad partial state)

`self_correction_arbiter.run()` **never raises**. A failure in the snapshot build, the consult, the
arbiter, or the apply degrades to **"do not hold dispatch"** — the loop falls back to its normal
DECIDE. A `frame_review` HOLD is only returned once it has been **safely recorded**; if recording
the hold errors, the loop **fails OPEN to normal dispatch** (holding-forever would be a DoS). The
`Loop._self_correction_live_holds_dispatch` hook wraps the call again as defense in depth. Covered by
the `test_run_*_fails_open*` tests and `test_loop_flag_on_hook_error_fails_open_to_normal_dispatch`.

## The flag

`AQ_SELF_CORRECTION_LIVE` (default **OFF**; truthy: `1`, `true`, `yes`, `on`). Deliberately
**separate** from `AQ_SELF_CORRECTION_SHADOW`.

- **Off** ⇒ complete no-op: the flag is checked first, so the snapshot is never read, `consult` is
  never called, and nothing is enqueued or recorded. The loop is byte-identical to stage-1
  (`test_loop_flag_off_is_complete_no_op`).
- **On** ⇒ the arbiter applies at most one bounded action by the priority above.

The `sibling_audit` and `escalate` paths do **not** disturb dispatch: with the flag on vs off, the
returned `Cycle`, the executor's schema-call sequence, and every mission-state DB surface are
identical — the only difference is the append-only audit-request + action rows
(`test_loop_flag_on_sibling_audit_enqueues_and_dispatch_proceeds`).

## Schema (migration `027_self_correction_arbiter.sql`)

Two append-only tables, granted to `aq_loop` exactly like `schema/026`:

- `self_correction_audit_request` — the sibling_audit queue. `UNIQUE (principle_id,
  source_correction)` idempotency; append-only trigger; `aq_loop` gets `INSERT` + sequence
  `USAGE, SELECT` only (no `UPDATE`/`DELETE`).
- `self_correction_arbiter_action` — the append-only "records why" trail for every applied action
  (escalate / hold_dispatch / enqueue_audit), same grants.

The enqueue uses an **un-targeted** `ON CONFLICT DO NOTHING` and detects insertion via `rowcount`
(no `RETURNING`) so `aq_loop` needs **INSERT only, not SELECT** — a targeted `ON CONFLICT (cols)` or
a `RETURNING` clause would demand SELECT and fail at runtime under `aq_loop` (PG16 infers the arbiter
index). `tests/test_self_correction_arbiter_pg.py::test_enqueue_and_record_run_under_aq_loop_privileges`
runs the real methods with the session role dropped to `aq_loop` so that regression is caught.

## Files

- `runner/consultants/self_correction_arbiter.py` — the pure `arbiter()` + `apply()` + fail-safe
  `run()` + the `AQ_SELF_CORRECTION_LIVE` flag.
- `runner/consultants/self_correction.py` — added the pure `partition_siblings()` /
  `sibling_audit_targets()` (single source of truth shared with `consult()`; consult behavior
  unchanged).
- `runner/loop.py` — the flag-gated `_self_correction_live_holds_dispatch()` hook, run **before**
  DECIDE so a hold skips dispatch entirely.
- `runner/db.py` — `enqueue_self_correction_audit_request()` (idempotent, INSERT-only) and
  `record_self_correction_arbiter_action()`.
- `schema/027_self_correction_arbiter.sql` — the two append-only tables + grants.
- `tests/test_self_correction_arbiter.py` (unit) and `tests/test_self_correction_arbiter_pg.py`
  (real-schema, gated on `AQ_SELFCORR_TEST_DSN`).
