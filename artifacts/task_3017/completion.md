# Task 3017 Completion Packet

## Summary

Implemented optional public-kit curiosity-as-bounded-appetite support for autonomy-quest v1.1.
Curiosity is off by default. When enabled, it has a bounded standing budget, validates an external
frontier, records cost as `work`/`runs` with `kind='curiosity'`, and stages output only through
`shared_learnings`. It never writes directly to live `learnings`.

## Contract Coverage

- Bounded appetite: `curiosity.budget.cycles_per_day` and `max_cost_usd_per_day`, enforced by
  `runner/curiosity.py` and `scripts/verify.sh`.
- Ground-truth-seeded frontier: `frontier.source` and `frontier.authority` are required; direct
  loop-owned frontier sources are rejected.
- Guardian-bounded: exploration uses the existing `Measure.satisfied()` / `overshooting()` helpers
  against a row-counted frontier metric; overshoot warns and skips.
- Falsifiable output: exploration replies are schema-bound and staged inertly in `shared_learnings`
  with `applies_when` and `falsified_by`.
- Cost tripwire: curiosity is skipped on mission soft-cap pressure and its own cycle/cost budget
  exhaustion; mission work proceeds.
- Ratchet: if no curiosity-origin staged learning is promoted through the behavior-change gate over
  the ratchet window after curiosity has actual run history, effective curiosity cycles shrink by
  `shrink_factor`.

## Files Changed

- `runner/config.py`
- `runner/curiosity.py`
- `runner/budget.py`
- `runner/db.py`
- `runner/loop.py`
- `runner/prompts.py`
- `scripts/verify.sh`
- `tests/test_curiosity.py`
- `interview/05-budget.md`
- `docs/doctrine.md`
- `README.md`

## Verification

- `python3 -m unittest discover -v` PASS, 7 tests.
- `python3 -m py_compile aq.py runner/*.py tests/*.py` PASS.
- `bash -n scripts/verify.sh scripts/healthcheck.sh scripts/apply_schema.sh scripts/model_ping.sh scripts/schedule.sh scripts/push.sh scripts/register_mcp.sh scripts/_env.sh` PASS.
- Curiosity verifier regex fixture PASS.
- `git diff --check` PASS.
- `./scripts/verify.sh` DID NOT PASS because this checkout has no `.env` / `AQ_DB_URL`; it exited
  78 before reaching the DB-backed checks. Run it in a configured kit DB environment before final
  merge.
- Formal `agent-review` evidence could not be produced here because this public repo does not have
  `scripts/agent-review` and `CODEX_HOME` is unset. A manual review pass found and fixed two issues:
  the verifier regex escaping and premature first-run ratchet shrink.

## Coordination

- First checkpoint sent to `ccf-kevin-bootstrap` and manager.
- `ccf-kevin-bootstrap` approved the plan and later approved the `[5/5] Curiosity` verifier gate.
- `ccf-1557-claude-takeover` sanity-check returned PASS against the six-clause contract and found
  no TalkingBack/internal leakage.

## Non-Blocking v1.2 Notes

- Add stronger frontier provenance validation to detect views or CTEs that launder loop-owned data
  behind an external-looking name.
- Consider shrinking curiosity budget immediately on frontier overshoot, not only through the
  promotion-count ratchet.
