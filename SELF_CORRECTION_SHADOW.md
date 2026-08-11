# Self-Correction Consultant — Stage 1: SHADOW (observe-only)

Task #4834, stage 1 of a two-stage make-it-live. This wires the **pure**
`runner/consultants/self_correction.py::consult()` into the live loop in **SHADOW mode**:
it observes what the consultant *would* recommend and records it. It changes **nothing**.

Stage 2 (the arbiter that may select and apply a recommendation) is **not** built here.

## What it observes

At the DECIDE step of `Loop.cycle`, when the flag is on, the hook:

1. Builds a **read-only** `SelfCorrectionSnapshot` from the governed causal-principle tables
   (`schema/010,021,022,024`), via two `SELECT`-only reads (`runner/db.py`):
   - `self_correction_recent_correction(window)` — the most recent **human correction**: a
     non-automatic `promote` transition where an independent adjudicator overturned a
     principle's prior automatic (`provisional`/`demoted`) disposition to `promoted`.
   - `self_correction_rule_principles(rule_version)` — every principle lineage minted under the
     corrected principle's generating rule, with its current classification and its own most
     recent human validation.
2. Runs the pure `self_correction.consult(snapshot)` (no DB/executor handle; returns a value).
3. Appends **one** row to `self_correction_shadow_log` describing the recommendation:
   `{observed_at, work_context, correction_item_id, generating_rule_id, result_kind, action,
   rationale, reopened_principle_ids, requires_human, detail}`.

### Governance → consultant mapping

| consultant model | governed principle tables |
|---|---|
| principle | a `(cause, effect, scope)` transition lineage in `causal_principle_transition` |
| `classification` | `to_status` of the lineage's latest transition (`provisional`/`promoted`/`demoted`) |
| `generating_rule_id` | `rule_version` on the lineage's `mined` transition (version suffixes collapse via `normalize_rule_identity`, so v1/v2 of a rule are one sibling class) |
| a human **correction** | the most recent non-automatic `promote` transition |
| `validated_classification` / `validated_at` / `evidence_ref` | a sibling's own most recent human `promote` transition |

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

## How stage 2 (live) will differ

Stage 2 adds the **arbiter** — the only component allowed to select and apply a recommendation
(the `#4834.details.shared_seam_contract` consultation chain). Where stage 1 only appends a
`self_correction_shadow_log` row, stage 2 will, under its own separate gate:

- feed the recommendation into the arbiter alongside the other pre-DECIDE consultants,
- and, when the arbiter authorizes it, actually **reopen the sibling principles for audit**
  (a governed, append-only lifecycle transition) rather than merely recording that it would.

Stage 2 does **not** relax any invariant here: the consultant stays pure, the shadow log stays
append-only, and applying a recommendation remains gated and reversible.
