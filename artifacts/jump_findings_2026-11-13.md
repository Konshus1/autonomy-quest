# Jump-Loop Run — 2026-11-13 06:28 EST

Cron run of the AQ jump-mechanism loop (Mission 1: model catalog watchdog).

## 1. Cycle runs

### 1a. Infra finding: aq_test schema was 26 migrations stale

First invocation of the loop failed 3/3 on
`UndefinedTable: relation "impasse_meta_mode_forecast_attempt" does not exist`
(`runner/db.py:490`). The aq_test database predated migrations 010–034.

- Ran `scripts/apply_migrations.py` (idempotent, sha-verified per file).
- Migration `010_business_benchmark.sql` failed on `column "customer_id" does
  not exist`: aq_test held a **legacy 5-row `subscriptions` table** (shape
  `id, customer, status`) that predates the current substrate.
- Resolution: `ALTER TABLE subscriptions RENAME TO subscriptions_legacy;`
  (zero data loss, fully reversible), then re-ran migrations. All 40 schema
  files now current: 000 → 034 + 999 + seed_structure.

### 1b. Cron-script drift finding (same class as the 2026-08-08 revert bug)

The cron job's STEP 1 snippet hardcodes `Gateway(inst.models)` +
`ApiExecutor(gw)` — the **metered API path driving `moonshotai/kimi-k3`** —
while `instance.yaml` has `engine.mode: subscription` /
`resident_agent: codex` since 2026-08-08 (added precisely because kimi-k3
cannot invoke tools). The API-mode fields in instance.yaml are documented
INERT in subscription mode, so the hardcoded path bypassed the intended
executor. `aq.py` uses `runner.executor.build(inst)`, which honours config.
Corrected this run to drive cycles through the proper entrypoint.

With the hardcoded API path, 3 cycles produced 3 real kimi-path defects:

| # | Symptom | Root cause |
|---|---------|-----------|
| 1 | `JSONDecodeError: Unterminated string ... char 2033` ×2 | `Gateway._call` hardcodes `max_tokens: 2000` (gateway.py:71,83); kimi's JSON reply truncates mid-string. No retry/continuation. |
| 2 | `AttributeError: 'NoneType' object has no attribute 'strip'` | Provider returned `None` content; `Gateway._json` does `text.strip()` unguarded (gateway.py:116). |
| 3 | `no verified price for moonshotai/kimi-k3 — cost recorded as 0. Budget is UNDERSTATED` | kimi-k3 absent from `data/models.json` price catalog; `Gateway._cost` correctly refuses to invent a price (gateway.py:95-103) — but the catalog gap means any kimi usage is unbudgeted. |

These reproduce the instance.yaml history (kimi cannot do tool use; the
watchdog mission needs the web) and remain latent as long as the API fields
stay inert.

### 1c. Subscription-mode cycles (proper path): 3/3 ran, 3/3 honest abstention

- `./aq.py once` ×3 → executor: `SUBSCRIPTION mode, driving codex
  (codex-cli 0.167.2, web search enabled)`.
- decide() created work #5, #6, #7 (`batch_research` variants, ≤20 models
  per batch). Each then hit **impasse STOP — `abstention_highest_net_value`**
  at its first plan step (identify_scope_and_store /
  locate_authoritative_records / identify_existing_scope).
- `impasse_meta_mode_forecast_attempt` rows #1–#3 recorded (this is the
  table whose absence crashed the pre-fix runs — the schema fix un-blocked
  meta-mode forecasting end to end).
- Why abstain (from forecast rationale): the loop's model of the world says
  the *authoritative research scope and record destination* are
  unestablished, human contact is not authorized, and duplicating the parked
  human request (work #4, `awaiting_human` since 2026-08-08) is expressly
  prohibited. It also flags `data/models.json` as a gateway cost cache, NOT
  a valid research destination. Abstain is the zero-incremental-cost
  baseline; every other mode is blocked or known_worthless.
- This is learning #5 (2026-08-08) applied correctly: *"if the tool manifest
  is unchanged, re-attempting the same work item will abort at zero cost
  indefinitely"* — the loop no longer burns cycles re-probing.
- Escalation ladder: `self-nudge (3 unproductive cycles)` at each cycle.
  Below the hibernate floor (8+, BB #561). No hibernation triggered.
- Measure unchanged: **0/20 models fresh within 30 days.** The unblocking
  lever is human: answer/deny the parked request in work #4 (the loop
  cannot re-ask by design).

## 2. T10 — conceptual inconsistency scan (causal_principle, postgres DB)

- 15 active principles, 105 pairs, 2 conflicts — identical result to the
  2026-08-08 baseline scan (corpus unchanged):
  1. `soft_tension`: `task_2074_terminal_human_signal_prevents_relaunch_candidate` ↔ `task_2074_concise_escalation_ask_candidate` (shared tags: advisory, candidate, closeout)
  2. `soft_tension`: `running_sink_may_be_dirty_checkout` ↔ `verified_recovery_over_apparent_recovery` (shared tags: reliability_impact, risk_cost, speed_to_validation)
- No direct conflicts. AQ's own cycle corpus has not yet grown the principle
  set (0 new principles mined today — cycles abstained before act/learn).

## 3. T11 — frame expansion (aq_test learnings)

- 3 episodes (the 2026-08-08 kimi-era learnings, all `generalisable`,
  confidence 0.75–0.85), 0 recurring gaps, 0 proposed dimensions.
- Expected: recurrence detection needs more episodes than 3. T11 stays
  silent rather than proposing noise — correct behavior, but the AQ corpus
  must grow before T11 has signal again.

## 4. Deltas vs last recorded run (2026-08-08)

- aq_test schema: stale → current (40 files, sha-verified). Legacy
  subscriptions preserved as `subscriptions_legacy`.
- Meta-mode impasse machinery: crashed on missing table → exercised
  end-to-end (3 forecast attempts recorded, all validated, abstain chosen
  with explicit wake conditions).
- Executor path: cron snippet driven the retired metered path → corrected
  to config-driven subscription path. Recommendation: cron STEP 1 should call
  `./aq.py once` (or `runner.executor.build`) rather than hand-building
  `ApiExecutor(Gateway(...))`, or the 2026-08-08 "uncommitted edit is not a
  durable change" failure mode recurs one layer up.
- kimi-k3 remains absent from `data/models.json`; its price is still
  unverified, so any API-mode use continues to understate the budget.

## 5. Addendum — 06:41 EST reproduction run (same morning)

An independent second run of the same cron loop, 6–13 minutes after the run
above, reproduced every finding with zero deltas:

- Drifted path (`Gateway(inst.models)` + `ApiExecutor(gw)` per the cron
  snippet): 3/3 cycles failed identically — 2× `JSONDecodeError` (unterminated
  string, char ~2000/2100 → the `max_tokens: 2000` truncation) + 1×
  `NoneType.strip` (provider returned `content=None`). Deterministic: the
  drift reliably manufactures the kimi defects each run.
- Proper path (`runner.executor.build`): 3/3 cycles → decide abstains
  (`do_nothing`), impasse stop on work #11 `confirm_scope_and_storage`
  re-confirmed with no new work rows created. The impasse system holds state
  across runs rather than re-proposing abandoned work.
- Escalation ladder steady at `self-nudge (3)` — abstain cycles create no run
  rows, so the streak neither grows toward hibernate nor resets. Guardian
  floor (BB #561) correctly not tripped.
- T10: identical (15 principles, same 2 soft tensions). T11: identical (3
  episodes, 0 gaps — corpus still too small).
- Conclusion: no unblocking happened between runs; the binding constraint
  remains the parked human request in work #4 (`awaiting_human` since
  2026-08-08). The loop is in a stable correct state, not a degraded one.

## 3. Run — 2026-11-13 06:56 EST

### 3a. Cycles (drifted path, third independent reproduction)

- Same deterministic defect pair: 2× `NoneType.strip` +
  1× `JSONDecodeError` (unterminated string ~char 2095 → `max_tokens: 2000`
  truncation). No cycles committed; DB unchanged at 3 runs / 3 learnings
  (all 2026-08-08).
- Escalation ladder steady at `self-nudge (3)` — hibernate floor (8+) not
  approached; guardian state correct (BB #561).
- Catalog warning persists every cycle: `no verified price for
  moonshotai/kimi-k3 — cost recorded as 0. Budget is UNDERSTATED.` Real
  watchdog finding for Mission 1 (Mission 1's own loop runs on kimi-k3, so
  the under-priced model is self-observing).

### 3b. T10 — unchanged

15 active principles, same 2 soft_tension conflicts:
`task_2074_terminal_human_signal_prevents_relaunch_candidate` ↔
`task_2074_concise_escalation_ask_candidate`; `running_sink_may_be_dirty_checkout`
↔ `verified_recovery_over_apparent_recovery`.

### 3c. T11 — NEW finding: the 0-recurring result is an extraction artifact

Previous runs reported "0 recurring gaps — corpus too small". That diagnosis
is wrong. The corpus is small (3 learnings) but the full attribute lists
overlap heavily:

- `attempting`: 3/3 episodes
- `blocker`, `manifest`, `dependent`, `flight` (from "pre-flight"),
  `deterministic`: 2/3 episodes each

The cron snippet's extractor takes the **first 5** matches of
`[a-z]{6,}` per episode, which lands on disjoint lead words
(`missing/capability/blocker/…` vs `exposes/definitions/…` vs
`declared/availability/…`) and destroys the overlap. With the top-5 cap
removed, T11 would see one coherent recurring theme chain: pre-flight probe
→ manifest-identical abort is deterministic → suspend + escalate rather
than re-queue. The 3 learnings are paraphrases of one principle, not three
unrelated gaps.

Action: extractor should rank attributes by cross-episode frequency (or
raise the cap), not take positional first-5. Not fixed in this run —
recorded here for the next cycle.
