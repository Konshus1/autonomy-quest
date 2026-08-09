# Automatic demotion on governed unproductivity

Source branch: `task/governed-principle-lifecycle`.

This run used a frozen copy of the actual Codex-subscription AQ cycle preserved in
`governed_principle_real_lifecycle.md`: `execute_local_sql`, run 1, productive, objective measure
`0 -> 1`, learning 1. The current store mined that copied real run, cross-tested it in a second SQL
environment, and promoted it through the independent gate.

## Policy

Production defaults are **N=5**, a **14-day rolling horizon**, and a **minimum 1-day observation
span**. Five resolved governed applications tolerate isolated misses; fourteen days retire a
persistently useless software rule inside a normal two-week iteration; the one-day span prevents a
rapid correlated retry burst from stripping authority. All values are configuration, validated at
startup. The integration demonstration set only `minimum_span_days=0` to accelerate time; the test
`test_default_minimum_span_blocks_burst_demotions` proves that the production default does not
demote the same five immediate failures.

## Actual integration output

For five iterations the management planning endpoint returned the exact promoted governor. The
trusted selection endpoint appended a pre-ACT receipt. Each bounded SQL action attempted an
idempotent duplicate insert, objectively measured `0`, and posted its goal outcome through the
normal `/api/causal/record-outcome` path.

```
automatic_demotion: [False, False, False, False, True]
trigger: unproductivity
metrics:
  selected: 5
  governed: 5
  successful_chains: 0
  horizon_days: 14
  selection_threshold: 5
history tail:
  validation -> promoted (noise) x5
  demote -> demoted (unproductive, automatic=true)
```

The full API responses and transition details are in
`governed_principle_unproductivity_real.json`.

## Specific mirror control

`test_high_selection_working_rule_survives_unproductivity_demoter` constructs six selected and
governed plans with one goal-reaching chain. It remains promoted with
`selected=6, successful_chains=1`. The demoter therefore distinguishes repeated use that never
pays off from repeated use that is working.

Additional guards prove assessment alone does not count, ambiguous governors receive no receipt,
unknown/replayed outcomes do not count, outcomes outside the horizon expire, a five-use burst is
blocked by the default minimum span, and concurrent threshold crossings create exactly one
demotion.
