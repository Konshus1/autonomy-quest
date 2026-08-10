# Checked mining, refutation, and unproductivity withdrawal

M5 restores the production lifecycle without returning raw transition or outcome DML to the manual
promoter. Each path is event-derived and owned by the NOLOGIN control principal.

* Mining remains provisional. `aq_loop` may request checked registration only for an exact causal
  edge derived from completed, successful, measure-moving runs and their durable learnings. SQL
  re-derives action, effect, provenance, direction, and certainty before appending `mined`.
* Validation evidence belongs to `aq_evaluator`. Its immutable observation/context is bound to the
  current mined/demoted generation. A separately checked wrapper appends validation and atomically
  withdraws promoted authority on a derived refutation (or failed negative control). The promoter
  cannot author or suppress this consequence.
* Completed governed runs enter a loop-owned immutable outbox in the same transaction as completion.
  The evaluator consumer accepts only a run ID, derives the plan goal result and exact authorization
  governors from PostgreSQL, and appends one immutable outcome. Five governed zero-goal outcomes in
  fourteen days spanning at least one day automatically demote that promotion generation. Abstain,
  block, defer, older generations, replay, incomplete runs, and caller-authored outcome fields do not
  count.

All three operations are replay-safe SECURITY DEFINER functions with pinned search paths and exact
principal allowlists. Automatic demotion is a withdrawal of authority, never automatic promotion.
