# Publish integration conflict resolutions

Integration branch: `task/publish-integration`

## G + A+B

A+B was a direct descendant of G (`merge-base == 9b72122`), so the merge was additive and had no
container conflict. G's Compose/auth substrate stayed intact; A+B added live plan predictions,
acquisition, intent evaluation, localized resolution, and consequence-gate migrations 010-014.

## A+B + Track E

Six text conflicts were resolved by contract rather than branch ownership:

- `runner/config.py`: kept A+B's canonical `$3 per plan` and discrete measured blast-level fields.
  Track E's scalar names remain accepted as compatibility fields, not as a second gate.
- `runner/prompts.py`: kept A+B's structured plan. Cost lives at `plan.expected_expense_usd` and
  blast radius is asserted per step; duplicate top-level model opinions were rejected.
- `runner/db.py`: composed both schemas in one write. Structured A+B values are authoritative;
  Track E's `expected_cost_usd` and `blast_radius` are persisted projections for the UI.
- `runner/loop.py`: kept A+B's sufficiency/acquisition/intent pipeline and added Track E's exact
  human-gate reasons and process heartbeat. The UI surfaces outlive a stopped loop.
- `tests/test_consult_act_wiring.py` and `tests/test_loop_approval_execution.py`: retained A+B's
  stronger plan-aware fakes and made their persistence doubles accept Track E's additive audit
  fields. Track E's gate tests were rewritten against the canonical structured plan contract.

Container-side Track E changes were accepted on merit: it supplies the zero-row flagship schema,
the real default benchmark mission needed by the one-command publish test, startup migration pass
for existing volumes, loop liveness, four UI surfaces, and observability-survives-loop behavior.
No customer/subscription rows are seeded.

The migration filename overlaps are safe but intentional: schema files are sorted by their full
names, so both `010_business_benchmark.sql` and `010_plan_predictions.sql` (and both 011/012 files)
run. Each is additive/idempotent and the startup runner reports every filename and digest.
