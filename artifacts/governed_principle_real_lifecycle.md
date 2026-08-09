# Governed principle lifecycle — real mission run evidence

Source ref: `task/governed-principle-lifecycle` at `38353926e71634d558400de84dd2775838f3f0ce`.
Database: isolated local PostgreSQL `aq_governance_real`. No live AQ checkout or production DB was mutated.
Executor: Codex CLI subscription mode (`python aq.py once`), not an API/metered call.

## Actual source cycle

The isolated AQ mission asked the resident agent to create one verified local catalog row. Actual
loop output:

```
INFO aq.executor: executor: SUBSCRIPTION mode, driving codex (flat rate, search included)
INFO aq.loop: cycle complete — run #1, cost $0, learned: When issuing multiline SQL through psql...
-> run #1 complete.
```

Post-run query:

```
kind=execute_local_sql run_id=1 succeeded=true productive=true
measure_before=0 measure_after=1 learning_id=1
```

`POST /api/causal/mine` returned one real loop-owned fuzzy principle,
`execute_local_sql -> measure_up`, with provenance `run_id=1/work_id=1/learning_id=1`.

## Lifecycle

1. The real run was recorded as the first shadow test (`+1`, `sql-catalog`). It carried no authority.
2. Promotion after only that environment returned HTTP 409.
3. A bounded SQL action in `api-sql` produced `+1`; the second canonical context/domain cleared the evidence breadth gate.
4. The independent promotion credential submitted an actually executed inverted-direction classifier: `['refutes','refutes']`. Promotion succeeded.
5. A duplicate insert in `queue-sql` measured `0`; it was classified noise and status remained promoted.
6. Deleting the queue row measured `-1`. The normal live `/api/causal/record-outcome` route automatically inserted `promoted -> demoted`; no promotion/human route was called.
7. Raw SQL attempting to null the promotion evidence reference failed: `causal_principle_transition is append-only`.

Actual concise output:

```
one_env_status 409
second_delta 1 noise_delta 0 refute_delta -1
auto_demote True
provenance PASS: causal_principle_transition is append-only
history [('mined','provisional','sql-catalog'),
         ('shadow_test','provisional','sql-catalog'),
         ('shadow_test','provisional','api-sql'),
         ('promote','promoted','governance'),
         ('validation','promoted','queue-sql'),
         ('demote','demoted','queue-sql')]
```

The full response/transition rows are in `governed_principle_real_lifecycle.json` beside this file.

## Deliberately broken versions

Detached worktrees at the committed implementation were mutated and the same targeted tests run:

* distinct environment/domain/context counts replaced by raw row counts: alias test failed because promotion incorrectly succeeded (`DID NOT RAISE PromotionRefused`, exit 1)
* `_is_new_domain` forced false: clear third-environment refutation test failed at `automatic_demotion is True` (exit 1)
* noise classified as refutation: over-demotion test failed at expected `noise` (exit 1)
* evidence reference made nullable and append-only trigger removed in a fresh DB: provenance test failed `DID NOT RAISE` (exit 1)

These are mutation controls, not reconstructions: each altered the executable version and produced a red test.
