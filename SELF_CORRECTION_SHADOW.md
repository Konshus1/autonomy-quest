# Self-Correction Consultant — Stage 1: SHADOW (observe-only)

Task #4834, stage 1 of a two-stage make-it-live. This wires the **pure**
`runner/consultants/self_correction.py::consult()` into the live loop in **SHADOW mode**:
it observes what the consultant *would* recommend and records it. It changes **nothing**.

Stage 2 (the arbiter that may select and apply a recommendation) is **not** built here.

## What it observes

At the DECIDE step of `Loop.cycle`, when the flag is on, the hook:

1. Builds a **read-only** `SelfCorrectionSnapshot` from the governed causal-principle tables
   (`schema/010,021,022,024`), via two `SELECT`-only reads (`runner/db.py`):
   - `self_correction_recent_correction(window)` — the most recent **correction**: a disposition
     **overturn**, i.e. the most recent transition that changed a principle's settled status
     (`from_status IS NOT NULL AND to_status <> from_status` — a demotion/withdrawal or a
     promotion). It returns the overturned lineage's **mined** transition id (the stable
     principle id) and **mined** `rule_version` (the generating rule — *not* the overturn
     transition's own `rule_version`, which differs: mined `v1` vs promote/demote `v2`).
   - `self_correction_rule_principles(rule_version)` — every principle lineage whose generating
     rule has the **same normalized identity** as the corrected principle's, with its current
     classification and its own most recent disposition validation.
2. Runs the pure `self_correction.consult(snapshot)` (no DB/executor handle; returns a value).
3. Appends **one** row to `self_correction_shadow_log` describing the recommendation:
   `{observed_at, work_context, correction_item_id, generating_rule_id, result_kind, action,
   rationale, reopened_principle_ids, requires_human, detail}`.

### Governance → consultant mapping

| consultant model | governed principle tables |
|---|---|
| principle | a `(cause, effect, scope)` transition lineage in `causal_principle_transition` |
| `classification` | `to_status` of the lineage's latest transition (`provisional`/`promoted`/`demoted`) |
| `generating_rule_id` | `rule_version` on the lineage's **mined** transition (version suffixes collapse via `normalize_rule_identity`, and the sibling read matches on that **normalized** identity — mirrored in SQL — so `v1`/`v2` of a rule are one sibling class) |
| a **correction** | the most recent disposition **overturn** — a demotion/withdrawal or a promotion (a transition where `to_status <> from_status`) |
| `validated_classification` / `validated_at` / `evidence_ref` | a sibling's own most recent disposition overturn |

**On trust and wording:** a correction is recorded by the trusted governance/evaluator principals
(e.g. `aq_governance` via `aq_control.promote_grounded_principle`, or the evaluator-owned automatic
withdrawal `aq-auto-demoter`). `aq_loop` **cannot write any principle transition** (`schema/999`
revokes all writes on `causal_principle_transition` from it), so the correction identity is
trusted — but it is a **service principal, not literally a human**. The `generating_rule_id` is
taken from the **mined** transition, never from the overturn transition (those `rule_version`s
differ, and taking it from the overturn is the exact bug the normalized read + this mapping fix).

`result_kind` is one of `recommendation` (action present, e.g. `sibling_audit`), `pass`
(explicit negative — evidence, not a missing return), `no_snapshot` (no recent correction, or
the corrected rule minted no principles → consult skipped), or `error` (a degraded observation).

## The zero-behavior-change guarantee (the load-bearing property)

**The loop's decision and every side effect are identical with the flag on vs off. The only
permitted difference is the append-only telemetry row.**

- The hook is placed immediately after the DECIDE executor call and **only reads** governance
  state, runs the **pure** consultant, and writes telemetry. It makes **no executor/model call**,
  touches no work/run/learning state, and returns nothing into the loop.
- It copies the causal-consult discipline (`loop.py` "telemetry must never become implicit STOP
  authority"): the entire hook is wrapped so **any** exception (snapshot, consult, telemetry) is
  non-fatal, logged at debug/warning, and the loop continues exactly as if the hook were absent.
- Proven by `tests/test_self_correction_shadow.py::test_zero_behavior_change_invariant_on_vs_off`,
  which runs a full loop cycle over identical state with the flag off and on and asserts the
  returned `Cycle`, the executor's schema-call sequence, and every DB mutation surface are
  identical — **except** the shadow log (empty off, one recommendation on).

## The flag

`AQ_SELF_CORRECTION_SHADOW` (default **OFF**). Truthy values: `1`, `true`, `yes`, `on`.

- **Off** ⇒ complete no-op: the flag is checked first; the snapshot is never read, `consult` is
  never called, no telemetry is written, zero overhead
  (`test_flag_off_is_no_op_consult_never_called`).
- **On** ⇒ identical behavior plus the telemetry row.

## Fail-safe

`self_correction_shadow.observe()` never raises. A failure in the snapshot read, the consult, or
the telemetry write degrades to a logged non-event (a best-effort `error` row where possible),
and the `Loop._self_correction_shadow` hook wraps the call again as defense in depth. Covered by
the `*_is_non_fatal*` tests and `test_flag_on_but_shadow_read_failure_does_not_disturb_the_cycle`.

## Grants

`schema/026` grants `aq_loop` exactly `INSERT` on `self_correction_shadow_log` **and**
`USAGE, SELECT` on its `bigserial` sequence (a table-only `INSERT` grant fails at runtime with
`permission denied for sequence` on `nextval`). No `UPDATE`/`DELETE` (the append-only trigger and
the missing grant both enforce that). The two reads use `aq_loop`'s pre-existing `SELECT` on
`causal_principle_transition`.

## Integration coverage

The unit tests hand-feed rows and cannot catch the `v1`(mined)/`v2`(overturn) `rule_version`
mismatch. `tests/test_self_correction_shadow_pg.py` (gated on `AQ_SELFCORR_TEST_DSN`) runs the two
**actual** SQL reads against a seeded governance DB end-to-end and asserts a real
`promote(v2)+mined(v1)` lineage with a real sibling yields a **non-empty snapshot → `sibling_audit`**
(not `no_snapshot`), and pins the pre-fix behavior (the exact-`rule_version` filter returns zero)
so the next SQL/schema drift can't silently reintroduce the bug.

## How stage 2 (live) will differ

Stage 2 adds the **arbiter** — the only component allowed to select and apply a recommendation
(the `#4834.details.shared_seam_contract` consultation chain). Where stage 1 only appends a
`self_correction_shadow_log` row, stage 2 will, under its own separate gate:

- feed the recommendation into the arbiter alongside the other pre-DECIDE consultants,
- and, when the arbiter authorizes it, actually **reopen the sibling principles for audit**
  (a governed, append-only lifecycle transition) rather than merely recording that it would.

Stage 2 does **not** relax any invariant here: the consultant stays pure, the shadow log stays
append-only, and applying a recommendation remains gated and reversible.
