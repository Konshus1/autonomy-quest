# DESIGN SPEC — Stages 7–8 as live loop steps: Evaluate (test-ladder + completeness) → Manager-gated merge

*Parallel-worker deliverable (2026-08-02), verified by the living manager. Implementation-ready. Cross-ref: DESIRED_STATE.md stages 8–10; WORKFLOW_MAP.md §1.*

## 0. Reality check — what exists + is reusable
The runner loop is a **single-agent** observe→decide→act→reflect→learn loop; nothing calls the merge gate today. Reuse:
- `runner/escalation.py::Escalation.was_productive()` — the base "did it actually produce" predicate (ground-truth, never self-report).
- `Escalation.assess()/unproductive_streak()/Verdict` — the rework→escalate ladder (SELF_NUDGE_AT=3, PEER_AT=5, HUMAN_AT=8, HIBERNATE_AT=12).
- `ralph_portable/manager_merge_gate.py::validate_manager_merge_decision()` — validates `{decision, manager_handle, cohort_id, rationale, uncertainty_note?}`; `ALLOWED_DECISIONS={approve_merge, escalate_human}`.
- `POST /api/manager/merge-decision` + `store.add_merge()` → `ralph_merges` jsonb; visible in `/api/ralph/state counts.merge_decisions`.
- `runner/causal_sync.py` — the template for a best-effort, never-fail management-API call from the loop.
- hrf-2878 Gate 3 (learning-completeness) / Gate 4 (model-coherence) are NOT in-repo yet; today their signal-class is measurement-only (`docs/curiosity.md:54`). Wiring them as hard predicates is new.

## 1. Insertion points (in `Loop.execute_work`)
- **STAGE 7 — Evaluate: on the critical path**, fold into the productivity computation (loop.py ~270-272) and commit the eval row **inside the same Invariant-1 tx** as `complete_run`+`write_learning` (so an evaluation can't exist without its run). Replace `productive = esc.was_productive(...)` with `Evaluator.evaluate(...)` returning `was_productive`'s bool **unchanged** plus a richer `EvalResult`.
- **STAGE 8 — Merge: post-commit, best-effort, wrapped** — a sibling of the causal post-commit block, before `check_soft_cap()`. The run is already durable; a merge-API hiccup must degrade to "no decision recorded", never fail the cycle. (Merge today = a decision RECORD, not a git merge — so emitting it post-commit is honest.)

## 2. Data model
New table `evaluations` (one row per run, written in the run tx): `run_id, cohort_id, test_level('ui'|'api'|'unit'|'none'), tests_passed(==was_productive base), intent_covered, completeness_ok(Gate3), coherence_ok(Gate4), verdict('pass'|'rework'|'escalate'), attempt, detail, created_at`. No new `runs` columns. No new `work.status` values (reuse pending|running|awaiting_human|done so zombie/reflection recovery is untouched). `cohort_id = f"cohort-run-{run_id}"` (cohort-of-one first slice). Merge record adds optional `run_id`/`evaluation_id` provenance keys the validator ignores.

Verdict → transition: `pass` → run `done` + emit `approve_merge`; `rework` → run recorded `productive=false` → existing ladder increments next cycle (the ladder IS the max-attempts bound; no dedicated counter); `escalate` → emit `escalate_human` (with required `uncertainty_note`).

## 3. Control flow
- **`runner/evaluate.py`**: `Evaluator.evaluate(...)` → `EvalResult(productive, test_level, tests_passed, intent_covered, completeness_ok, coherence_ok, verdict, detail)`. `tests_passed = esc.was_productive(...)` (REUSE). `classify_test_level`/`check_intent_coverage` stubs (intent stub→True, honest partial). Verdict: rework if not tests/intent/completeness; escalate if coherence fails; else pass. `productive=tests_passed` returned unchanged so the ladder/hibernation/verify.sh are untouched.
- **`runner/merge_sync.py`** (mirrors causal_sync): `build_merge_packet(pass→approve_merge | escalate→escalate_human+uncertainty_note)`, `submit_merge_decision(base,packet)` best-effort. Validate locally (`validate_manager_merge_decision`) before POST; the endpoint re-validates (both-sides). `rework` emits nothing.

## 4. hrf-2878 Gate 3 / Gate 4 slot-in (at the evaluator's completeness gate)
- **Gate 3 (completeness_ok):** reads the run↔learning join + reflect insight; predicate = a non-trivial learning covering intent (evidence non-empty, confidence present, scope known). Stub until the module lands. Fail → `rework`.
- **Gate 4 (coherence_ok):** reuses the existing consult/surprise (`predicted_certainty` vs `actual_success=measure_after>measure_before`); high surprise on a high-certainty edge = model disagrees with reality = a close call. Fail → `escalate` (the `escalate_human`+`uncertainty_note` path). Gate 4 READS the surprise; never writes support or promotes.
Both are single bools on `EvalResult`, so dropping in the real modules is a body-swap with no loop/DB/packet change.

## 5. Smallest first slice + tests
**Goal:** every productive+coherent+complete cycle emits one `approve_merge` into `ralph_merges`; incoherent → one `escalate_human`; rework → nothing (climbs the ladder). Cohort-of-one; no worktrees; no git merge.
**Deliverables:** `runner/evaluate.py`, `runner/merge_sync.py`, `runner/db.record_evaluation` + `schema` table, two `loop.py` wiring edits, `Instance.ralph_control_enabled`/`ralph.manager_handle`.
**Tests:** `evaluate` returns `productive` byte-identical to `was_productive` across its truth table (no drift); verdict rework/escalate/pass paths; eval row committed in the run tx; `build_merge_packet(pass)` passes the validator; `build_merge_packet(escalate)` carries `uncertainty_note`; `submit_merge_decision` returns None + raises nothing when unreachable; loop integration: productive⇒1 approve_merge + run done + join intact; unproductive⇒0 merges + streak incremented; endpoint 503⇒cycle still returns its Cycle.
**Do NOT claim:** worktree isolation, multi-unit cohorts, real branch merges, enforced intent-coverage — later slices.
