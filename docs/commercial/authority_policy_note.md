# AQ AuthorityPolicy inputs for Ralph V2 (#6076/#951): intent-verifier + execute-to-learn

For @pasf-codeflow-thoughts per bus #60752. This is AQ's already-built (#4834) authority logic, described as an Activity-side, typed-verdict `AuthorityPolicy` adapter. All of it is deterministic given (plan, typed mission semantics, DB-measured before/after); the only LLM-authored input is the plan itself, which is DATA the policy validates — never authority. Source files: runner/intent_lineage.py, runner/planning_sufficiency.py, runner/consequence_gate.py, runner/loop.py.

## 1. Intent verifier (anti-Goodhart) — `verify_intent_lineage(plan, authoritative_concerns)`
GROUND-TRUTH INPUTS (not model prose):
- authoritative concerns from typed mission semantics (`mission_intent_contract`): always `serve_mission_progress = {metric: mission_delta, op: >=, value: 0}`; for reach_and_maintain+target also `must_not_overshoot = {metric: mission_value, op: <=, value: target}`.
- the plan's machine predicates: `goal_predicate`, each subgoal `success_predicate` (+ `serves_concern_ids`), steps linked to subgoals.
PREDICATE: the conjunction of [goal_predicate] + subgoal success_predicates must FORMALLY ENTAIL each authoritative concern predicate (`predicates_entail` over numeric interval bounds). Vacuous/contradictory criteria are rejected (no proof from an impossible antecedent). Key subtlety: a `mission_value == target` predicate does NOT entail `mission_delta >= 0` (you can reach a value by falling), so a serve concern requires an explicit `mission_delta` predicate.
TYPED VERDICT: `{intent_valid: bool, reasons: [str]}`.
GROUND-TRUTH GRADING (the anti-Goodhart teeth): at MEASURE time the loop overwrites any model-claimed metric with a DB read (loop.py ~843-847): `mission_value = measure_after`, `mission_delta = measure_after - measure_before` (`measure_before` is itself a DB read). An ACT response cannot buy progress by claiming a convenient delta. In V2 terms: the deterministic workflow consumes the typed verdict; the model's plan/claims never bypass the measured verdict.
FAILURE/HOLD: intent_valid=False → reject before ACT ("satisfying plan criteria does not guarantee concern <id>"). A known causal relation predicting the OPPOSITE direction → hard conflict → blocked (not merely unresolved).

## 2. Execute-to-learn cold-start gate — `_frame_gaps_are_reversibly_self_observable(work, assessment)`
PROBLEM IT SOLVES: a step is a "frame gap" when NO learned causal relation backs it (planning_sufficiency: EdgeState.ABSENT). On a fresh mission the causal graph is empty → every step is a frame gap → routed to a value-of-information acquisition machinery whose channels must NOT perform the target action → deadlock (never acts → never learns → never acts).
REVERSIBLE/AUTHORIZED/LOW-CONSEQUENCE CRITERIA (all required; conservative — any non-qualifying frame-gap step falls back to full acquisition): work.reversible AND NOT spends_money AND NOT touches_human AND NOT commits AND NOT requires_human; AND for EVERY frame-gap step, the canonical `assess_blast_radius(step.blast_radius).level <= 2`. That assessor FAILS CLOSED: level 3 for irreversible_external_write / public_or_unbounded / production_wide / >10 entities / MISSING or UNKNOWN blast keys (opaque facts). Reusing the canonical assessor guarantees the gate and the execute-to-learn predicate can never diverge.
TYPED VERDICT: `{may_execute_to_learn: bool}` (per work, gating on all its frame-gap steps).
BEHAVIOR: qualifying → execute the reversible action directly and observe (the before/after IS the acquisition; it also seeds the causal graph so the step is no longer a frame gap next time). The consequence gate still fires AFTER as an independent backstop.
FAILURE/HOLD: fail-closed — non-qualifying or opaque-blast → the full acquisition/VOI path. Flag-gated `AQ_EXECUTE_TO_LEARN` (default OFF).
HONEST CAVEAT (matters for an AuthorityPolicy): both this predicate and the consequence gate derive from the MODEL's own blast/reversible declaration on the step. So "anything irreversible still takes the full path" holds only under honest declaration. In V2 you may want a cross-check of the declared blast against the actual Activity effect (idempotency-key + destination readback would catch an under-declared write after the fact).

## 3. Consequence gate — `assess_plan_gate` / `human_gate_reasons`
Deterministic: expense threshold + max per-step blast level vs the autonomy gate level; parks for human on breach or on `requires_human`. Independent of the two above — it is the last backstop. In V2 this maps to a consequence-authority verdict the workflow consumes before any mutating Activity.

## How this fits the V2 constraint you stated
Each of the three is an Activity-side authority verdict producing a TYPED result (intent_valid / may_execute_to_learn / gate_reasons). When LLM-backed the input is the model-authored plan + (for future use) prompt evidence; the deterministic workflow consumes only the typed verdict and cannot let a model response bypass ownership, exact-SHA, consequence, or human gates. This mirrors AQ's own invariant (an ACT response cannot overwrite DB-measured metrics; gates recompute from truth).

## Open coordination
Converging AQ's live workflow seam onto your full portable WorkContract shape (versioned WorkContract; source_system/source_task_id; engine/workflow_execution identity; expected generation+lease; authority scope; idempotency key; exact source/candidate/deployed SHA; evidence refs; destination readback; retryability; retirement/cancellation receipts) is a code change on AQ (#4834) — queued for Kevin's go; AQ #4834 currently has a pending land decision (human review #31). Reference: BB #2702 (AQ #4834 state).
