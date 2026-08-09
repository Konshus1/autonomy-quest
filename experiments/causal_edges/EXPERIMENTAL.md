# EXPERIMENTAL — Causal-Edge AGE Schema + Judgment-Only Planning Check

> **STATUS: EXPERIMENTAL. NOT PART OF MAIN OPERATIONS.**

This directory is an **experimental, additive-only** extension to the
autonomy-quest repo. It implements T5 (#4897): the causal-edge AGE schema
substrate and a **record-only** judgment planning check.

## What this is

- A new schema migration (`009_causal_edges.sql`) that adds **two new tables**
  (`causal_edge`, `planning_prediction`) to the AGE graph substrate. No existing
  table (001–008) is altered. The migration is purely additive.
- A **record-only** planning-check module (`planning_check.py`) that, given a
  plan, reads governing causal edges and records a *predicted certainty* plus a
  **full outcome representation** (which principles will be supported /
  challenged, which edges will be stressed) — per the JEPA enrichment
  (BB decision #829). It **does not change any actuator path**. It writes
  predictions only; it never gates, blocks, or redirects a plan.

## What this is NOT

- It is **not** wired into `runner/loop.py` or any main running path.
- It does **not** change executor, gateway, budget, escalation, or sharing
  behavior.
- It does **not** promote, demote, or falsify edges at runtime — that is a later
  slice (BB #746 build direction step 2).
- It is **not** loaded by `scripts/apply_schema.sh` (which applies
  `schema/*.sql`). It lives under `experiments/` deliberately.

## Design sources

- **BB decision #746** — the causal-edge model: one edge = two linked claims
  (action→direct effect is mechanical/scriptable; direct effect→mission measure
  is probabilistic goal-potency). Three independent weights:
  `formality_weight` (epistemic confidence), `strictness_weight`
  (advisory / soft-constraint / hard-guardrail), `directness_weight`
  (judgment → predicate → script). Executor slot A (actuator) / B (verifier) /
  C (constraint). Surprise fields: predicted certainty vs actual → surprise →
  gated weight update. Bookkeeping: scope/conditions, evidence_run_ids,
  support_count, falsified_by, last_validated.
- **BB decision #829 (REC 2)** — enrich T5's predicted certainty from a scalar
  to a **full outcome representation**: which principles supported/challenged,
  which edges stressed. One prediction record per plan.

## Constraint contract (Kevin's rule)

- Additive migrations only — new schema file, **no edits to 001–008**.
- Planning check is **record-only** — no actuator path change.
- Demo lives in `experiments/` with this EXPERIMENTAL marker.
- Acceptance includes a main-path regression check.
- Main repo operations must stay functional.
- **Constraint violation = stop.**

## Files

| file | purpose |
|------|---------|
| `009_causal_edges.sql` | additive migration: `causal_edge` + `planning_prediction` tables |
| `planning_check.py` | record-only judgment planning check |
| `test_planning_check.py` | tests: migration runs, predictions recorded, main-path unaffected |
| `EXPERIMENTAL.md` | this marker |
