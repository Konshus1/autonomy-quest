# Jump-Mechanism Live Findings — AQ Loop Cron Run

**Date:** 2026-08-08 (cron jump-loop run)
**Branch:** task/jump-mechanism-live-wiring
**Mission:** AQ Mission 1 — Model Catalog Watchdog (instance.yaml, restored from instance.yaml.bak)
**DB:** aq_test (3 runs, 3 learnings, 20 in-scope research items, **0/20 fresh**)
**Engine:** api / openrouter / moonshotai-kimi-k3

## Summary

Ran 3 AQ loop cycles. **No new runs were recorded** (DB unchanged: 3 runs, 3 learnings).
Two cycles returned `None` (the decide phase honestly chose `do_nothing`); one cycle
crashed in the decide phase with an unrecorded `AttributeError`. T10 and T11 were then
run against the real corpora. Two NEW concrete findings emerged — one robustness bug in
the gateway, one budget-honesty gap in the model price table.

---

## 1. AQ Cycle Results

| Cycle | Result | Detail |
|-------|--------|--------|
| 1 | None (do_nothing) | decide chose no work; no run row |
| 2 | **crash** | `AttributeError: 'NoneType' object has no attribute 'strip'` at `runner/gateway.py:116` |
| 3 | None (do_nothing) | decide chose no work; no run row |

Escalation ladder printed `self-nudge (3 unproductive cycles)` before each decide call —
the streak from the 3 earlier honest failures (runs 1-3) is unchanged because do_nothing
cycles create **no run rows**, so the ladder does not climb while the loop is idle.

The capability-gap pattern persists exactly as the prior learnings predicted: kimi-k3 has
no web tool, so the watchdog cannot verify OpenRouter prices/context windows. The loop
has stopped attempting the impossible: work #4 (`blocker_escalation_and_tool_free_batch_prep`)
is parked `awaiting_human`, and the decide phase now declines work rather than burning
cycles on an impossible plan. This is the guardian floor working as designed (BB #561) —
not hibernated (HIBERNATE_AT=12, streak is 3), but blocked on a human to attach a real
web/shell capability.

## 2. NEW FINDING — Gateway crashes on null model content (unrecorded cycle death)

**Where:** `runner/gateway.py:89` reads `d["choices"][0]["message"]["content"]`; OpenRouter
returns `null` intermittently for kimi-k3 (reasoning model — content can be empty while
reasoning tokens are emitted). `_json()` at line 116 then does `text.strip()` on `None`.

**Why it matters:** the crash happens in the **decide** phase, BEFORE `create_work` /
`start_run`. The loop's fail-loud invariant ("A cycle that dies leaves a row saying it
died") is only honored for act/reflect failures inside `execute_work`; a decide-phase
crash is silently lost — no run row, no learning, no escalation credit. The loop docstring
promises otherwise.

**Evidence:** full traceback captured 2026-08-08 during cycle 2:
```
File "runner/loop.py", line 148, in cycle       decision, u_decide = self.ex.run(...)
File "runner/executor.py", line 552, in run     return self.gw._json(text), usage
File "runner/gateway.py", line 116, in _json    s = text.strip()
AttributeError: 'NoneType' object has no attribute 'strip'
```

**Suggested fix (not applied — out of loop scope):** guard in `_json` (`if not text: raise
AgentFailed("model returned null content")`) so the cycle is recorded as a failure instead
of dying silently; or retry once with `max_tokens` raised, since truncation into a
reasoning-only reply is the likely trigger.

## 3. NEW FINDING — kimi-k3 missing from data/models.json (budget understated)

**Where:** `runner/gateway.py:95-104`, `_cost()` — the price table `data/models.json` has
23 models; `moonshotai/kimi-k3` is NOT among them.

**Effect:** every API call logs
`no verified price for moonshotai/kimi-k3 — cost recorded as 0. Budget is UNDERSTATED.`
The loop's daily-soft ($0.50) and monthly-hard ($2.00) caps are therefore NOT actually
enforced for the model in use — the exact class of gap the watchdog mission exists to
catch, now observed on the loop's own budget. Adding a price requires verified sourcing
(per the mission's own rules — no fabricated prices), so this is a real, actionable gap
for a human/verified-catalog pass.

## 4. T10 — Causal-Principle Inconsistency Scan (postgres.causal_principle)

- **15 active principles, 2 soft tensions, 0 direct conflicts** — identical pairs to the
  previous scan (commit eec386a); the principle corpus has not changed since then.
- Pairs: `task_2074_terminal_human_signal_prevents_relaunch_candidate` ↔
  `task_2074_concise_escalation_ask_candidate`; `running_sink_may_be_dirty_checkout` ↔
  `verified_recovery_over_apparent_recovery`.
- The "penalize" edges the loop's failures were expected to feed T10 have NOT materialized
  yet: no new principles were mined because no run has been productive, and no T3 mining
  pass has run against the aq_test failures. The capability-gap learnings (runs 1-3)
  remain uncaptured as causal principles.

## 5. T11 — Frame Expansion on AQ Learnings (aq_test.learnings)

- **3 episodes scanned, 0 recurring gaps, 0 proposed dimensions.**
- Corpus too small for recurrence: only 3 learnings exist, all about the same blocker
  (tool non-invocability), so attribute extraction produced no ≥2-episode mismatch beyond
  the same root cause. No dimension proposal fired. Nothing to promote (DR12 moot).

## 6. What This Means / Next Steps

1. **Fix the gateway null-content crash** (Section 2) so decide-phase failures are
   recorded, not silent — this is a one-spot robustness fix in `_json`/`_call`.
2. **Add a verified kimi-k3 price to data/models.json** (Section 3) or the budget caps
   are fiction for this model.
3. **Attach a web/shell capability** (or switch `engine.mode` to `subscription`) to
   unblock the research mission: until then the watchdog stays at 0/20 and the loop
   correctly refuses to spend on impossible plans.
4. **Let T3 mining capture the tooling-gap learnings** as causal principles so T10 gets
   the penalize edges the failures are producing (currently 0 new edges).

## Honesty note

All numbers above are read live from the DBs (aq_test, postgres) and from actual run
output captured during this cron session. No fabricated runs, prices, or tensions.
