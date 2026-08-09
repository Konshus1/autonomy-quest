# Real PostgreSQL impasse meta-mode proof

This is a deterministic, subscription-shaped proof through the production `Loop.cycle`, `Db`,
evaluator, migrations, and transaction path. It is not evidence that model forecasts are calibrated.

## Command

```bash
export AQ_STATE_DIR=/tmp/aq-voi-compose-state
scripts/compose-with-secrets.sh -p aqvoiproof up -d postgres migrate
scripts/compose-with-secrets.sh -p aqvoiproof run --rm --no-deps \
  --entrypoint python migrate /app/scripts/prove_meta_mode_cycle.py
scripts/compose-with-secrets.sh -p aqvoiproof down -v
```

Fresh Compose output confirmed `impasse_meta_mode_decision` existed and the new rung constraint was
installed. The proof emitted the full JSON in `impasse_meta_mode_real.json`.

## Completion result

First impasse comparison (common conservative net utility points):

```text
environment_experiment  5   <- chosen
human_question           2
autonomous_practice      2
internal_computation     1
human_demonstration      0
abstain                   0
goal_relaxation         -1
```

Internal computation was cheapest (cost 1 versus experiment cost 3), but the controller selected
the higher-net experiment, proving the fixed cheapest-first ladder did not determine choice. The
selected forecast's exact instruction, `$1.25` expense, blast level 1, and spending flag entered the
normal autonomy gate. The unsupported target was recorded as `executed=false`.

After the experiment committed its run, learning, acquisition result, and observation, all modes
were rescored:

```text
abstain                   0   <- chosen STOP
internal_computation     -2
goal_relaxation          -2
environment_experiment   -3
human_question           -3
autonomous_practice      -4
human_demonstration      -4
```

The second decision was durably `abstain`, reason `no_option_worth_cost`; it created no ACT run.
Both forecast calls preserved `tokens_in=11, tokens_out=7`, including the stop-only call, in
linked forecast-attempt rows.
Decision and prediction timestamps preceded ACT. After STOP, the proof inserted a newer matching
causal edge backed by the completed acquisition run; `wake_impasse_stops()` reopened the exact work
from `abandoned` to `pending`, then the synthetic edge was removed.

## Recovery control

```bash
AQ_PROOF_FAIL_FIRST_ACT=1 ... python /app/scripts/prove_meta_mode_cycle.py
```

The first ACT raised after the durable selection. A new `Loop` object recovered the same pending
acquisition without a duplicate forecast or decision row, completed it, then rescored and stopped.
Output remained two meta calls and `[acquire environment_experiment, abstain abstain]`.

## Sequential-expense control

With `-e AQ_PROOF_REPEAT_EXPENSE=1`, a second new observation selected the same experiment channel
again. PostgreSQL recorded two completed acquisitions and two independent incurred reservations,
`$1.250000` and `$2.500000`, followed by abstention on the third forecast. This control would fail
under the former work-id-keyed reservation path.

## Claim ceiling

The controller and transaction path are real. The utility intervals in this proof are frozen
fixture values, not calibrated empirical forecasts. Evidence references and consequence forecasts
remain forecaster-declared. Autonomous practice has a required trials/holdout/evidence receipt but
no OS-level sandbox-isolation proof. See `docs/design/impasse-driven-meta-mode.md`.
