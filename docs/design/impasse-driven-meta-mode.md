# Impasse-driven experimentation and net-value meta-mode selection

## Claim ceiling

Impasse-triggered inquiry is prior art. BIPLEX, Curiosity-Driven Imagination, and RAPid-Learn
already connect planning or execution impasses to experimentation, operator discovery, or recovery.
Common expected-utility VOI, value of computation, sequential stopping, human-query valuation,
practice, abstention, and goal relaxation also have established literatures. This implementation is
**not a new decision-theoretic formalism**. We did not locate an implemented planner-impasse
controller that evaluates all of these channels together under one operational contract; that
integration, its calibration, and its empirical comparison with fixed ladders are the research
candidate.

Use findable terms: **impasse-driven experimentation**, **plan-failure recovery**, **open-world
adaptation**, and **rational metareasoning**. Do not call the contribution “planning-triggered
curiosity.”

## From order to comparison

The original acquisition ladder tried `recall -> search -> bounded experiment -> analogy -> human`
in a fixed cheapest-first order. The scored controller compares:

1. internal computation;
2. environment/tool experiment;
3. human question;
4. human demonstration;
5. autonomous practice with a sandbox transfer test;
6. goal relaxation;
7. abstention.

After every durable observation, it forecasts and compares the available set again. A selected
acquisition replaces only the unsupported target step and still passes the unchanged autonomy gate.
Abstention is a terminal choice, not an information source. Goal relaxation persists a structured
proposal, parks the work, and notifies a human; it does not silently mutate the mission. Normal
approval acknowledges and retires that proposal without ACT or rescore. Applying it requires an
explicit operator edit to mission configuration and a new plan. Retired goal proposals are
excluded from causal-evidence wake-up and cannot become autonomous target work later.

## Common scale

The exact Bellman-optimal model would be a risk-constrained belief-state SMDP. Its acquisition score
is incremental value relative to stopping now:

```
NVOI(a | belief) = Q(acquire a, belief) - V(stop now, belief)
```

AQ implements a deliberately myopic, interval-valued approximation because its outcome models are
not calibrated enough to justify precise probabilities. Forecasts use coarse **mission-utility
points**, 0 through 4 per component. One point is one declared band of progress
toward the current mission goal over its finite horizon. Every bounded option supplies:

- `D=[Dlo,Dhi]`: direct mission value of the acquisition itself;
- `I=[Ilo,Ihi]`: improvement in later decisions, not entropy or facts learned;
- `C=[Clo,Chi]`: incremental opportunity cost on the same rubric, including compute, money,
  latency, human burden, and reversible risk.

The auditable score is conservative:

```
net_interval = [Dlo + Ilo - Chi, Dhi + Ihi - Clo]
score = lower(net_interval)
```

The controller selects the highest score, then the highest upper bound, then a stable canonical
mode. Hard authority, privacy, irreversibility, and safety limits are constraints (`blocked`), never
prices. The selected forecast's exact instruction and declared expense, blast, reversibility,
spending, human-contact, and commitment fields are persisted and passed into the unchanged autonomy
gate. These consequence forecasts are still model-declared and require independent calibration;
the selector does not make them attested facts. Sunk costs are excluded. Abstention is explicitly scored as the zero incremental baseline,
so if every action has a negative best case it wins rather than the controller selecting a
least-bad action.

This scale is only defensible when the mission owner accepts the coarse utility rubric. That
acceptance/calibration is not yet represented in mission configuration; the current controller
validates and persists forecaster-supplied bounds and the proof uses preregistered fixture bounds.
Evidence refs are auditable strings, not independently attested estimates. If cross-channel
trade-offs cannot be elicited honestly, the correct result is a constrained/Pareto ambiguity, not
a fabricated scalar.

## Unknown is not worthless

Each estimate has one of four states:

- `bounded`: auditable intervals and provenance exist;
- `known_worthless`: evidence certifies the upper net bound is non-positive;
- `blocked`: infeasible or outside a hard boundary;
- `unknown`: no defensible bound exists.

`unknown` has no numeric score and requires a wake condition. It is never coerced to `[0,0]`. The
current conservative policy stops with `unresolved_unknown` rather than pretending the option is
worthless. A matching new causal-edge observation reopens the stopped work automatically; other
free-text wake conditions remain audit records, not executable watchers. A future calibrated
implementation may value a bounded probe of that unknown, but it must show how the probe can change
a downstream decision.

## Explicit VOI/VOC stopping

Every successful acquisition result, run, and learning commits atomically. Every forecast call is
persisted as an attempt before semantic selection validation, so invalid all-mode/interval responses
are charged and parked rather than retried indefinitely. Valid attempts link to their decision;
forecast cost is counted once. Each selected acquisition reserves external expense under its own
meta decision, so repeated modes cannot reuse the parent work reservation. The hard cap is checked
again after forecasting and before ACT. Pending selected acquisitions recover across ACT
failure/restart without rescoring. On the next turn the same durable
plan is reassessed and all options are rescored with the observation history. The controller stops
when abstention is highest (`no_option_worth_cost` or `abstention_highest_net_value`), when a
feasible estimate remains unknown (`unresolved_unknown`), or when no option is feasible. Each stop
record preserves all scorecards, policy version, reason, and wake conditions.

## Evidence and limitations

`scripts/prove_meta_mode_cycle.py` uses the real PostgreSQL `Db` and production `Loop.cycle` path.
Its first decision chooses a costlier environment experiment (net 5) over cheaper internal compute
(net 1); after the durable observation, its second decision explicitly chooses abstention because
every remaining option has negative best-case net value. The unsupported target action never runs.

The proof is deterministic and subscription-shaped, not evidence of calibrated real-world utility
forecasts. It has been run from a fresh Compose PostgreSQL/AGE migration. A repeat-channel control
persists two independent expense reservations ($1.25 and $2.50); a crash control recovers the same
selection without another forecast. An existing per-decision reservation is idempotent on restart. At the
exact hard cap, metered ACT/REFLECT remains blocked because it would be new spend; no global
reservation bypass exists. The immutable mode instruction is prepended to forecast detail.
Autonomous-practice execution requires recorded sandbox-trial count, holdout transfer score, and
evidence; this is a receipt contract, not proof of OS-level sandbox isolation. Human-question and
human-demonstration modes likewise require synchronous durable answer/trace receipts. AQ does not
yet implement an asynchronous human-response inbox, so a request without its response is
quarantined for review rather than counted as information. The next research phase must measure forecast calibration, realized regret/utility,
human burden, harm, latency, and stopping quality against the old fixed ladder and single-channel
policies. A null is acceptable.

## Literature anchors

- Howard (1966), information value theory.
- Russell & Wefald (1991), rational metareasoning; Hay et al. (2012), selecting computations.
- Guez, Silver & Dayan (2012), Bayes-adaptive RL; Klenske & Hennig (2016), dual control.
- Cohn, Durfee & Singh (2011), action-query strategies; Scerri et al. (2002), adjustable autonomy.
- Chow (1970), reject/abstention trade-offs; Smith (2004) and Keyder & Geffner (2009), soft goals.
- Sarathy & Scheutz (2022), BIPLEX; Lorang, Lu & Scheutz (2025), open-world operator discovery.
