# Checked mining, refutation, and unproductivity withdrawal

M5 restores the production lifecycle without returning raw transition or outcome DML to the manual
promoter. Each path is event-derived and owned by the NOLOGIN control principal.

* Mining remains provisional. `aq_loop` may request checked registration only for an exact causal
  edge derived from completed, successful, measure-moving runs and their durable learnings. SQL
  re-derives action, effect, provenance, direction, and certainty before appending `mined`.
* Validation evidence belongs to `aq_evaluator`. Its immutable observation/context is bound to the
  current mined/demoted generation. A checked wrapper appends validation and atomically withdraws
  promoted authority on a derived refutation or failed negative control. Exact historical replay
  returns the recorded consequence but cannot count in a later mined/demoted generation.
* Every completed checked run gets an immutable outcome event from a control-owner completion
  trigger, with a deferred database constraint as the backstop; normal, failure, quarantine, and
  interrupt paths cannot omit it. Migration backfills exact pre-M5 checked completions. Python call
  ordering is not the guarantee. Exact work, plan, run, evaluation, and
  authorization binding is rechecked. The trigger carries only a run ID and cannot state success.
  The separate evaluator service receives the same read-only deployed/interviewed mission config,
  reads that mission's live measure query itself, and writes an immutable private observation and
  evaluator clock. For reach-and-maintain goals it compares that
  independent value with the configured/query target. A maximize goal without an independent
  baseline is recorded `not_evaluable`, not guessed from loop-authored metrics. Five governed,
  eligible, independently observed zero-goal outcomes in fourteen days spanning at least one day
  demote only their exact promotion generation.
* Startup and each trigger drain the durable backlog in bounded batches. Proven invalid/cross-wired
  events are evaluator-quarantined one at a time and cannot poison later work. Transient measure,
  configuration, or database outages remain pending for retry while later rows continue.
  Concurrent/replayed service calls return the same first immutable application even when a retry
  generated a fresh receipt and clock.

Threat boundary: `aq_loop` is the operational producer and can stop or corrupt its own work, but it
cannot author evaluator result/time fields, omit a checked-run event, mutate a private observation,
or create authority. Quarantine and evaluator outage fail toward less automation. The app holds only
a narrow run-ID trigger capability; ACT receives neither it nor an evaluator credential.

All operations are replay-safe SECURITY DEFINER functions with pinned search paths and exact
principal allowlists. Automatic demotion is a withdrawal of authority, never automatic promotion.
