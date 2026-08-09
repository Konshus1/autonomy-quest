-- 009_causal_edges.sql — EXPERIMENTAL, ADDITIVE.
--
-- Implements T5 (#4897): the causal-edge AGE schema substrate.
-- Source of truth: BB decision #746 (causal edge model) + #829 (JEPA enrichment).
--
-- THIS MIGRATION IS ADDITIVE ONLY:
--   * It creates TWO NEW TABLES (causal_edge, planning_prediction).
--   * It does NOT alter, drop, or rename any table from 001–008.
--   * It does NOT touch work / runs / learnings / measurements / shared_learnings.
--   * It is NOT loaded by scripts/apply_schema.sh (which globs schema/*.sql).
--     It lives under experiments/causal_edges/ and is applied explicitly by tests.
--
-- A causal claim is ONE EDGE in the graph (AGE), decomposed into TWO LINKED CLAIMS:
--   action -> direct effect   : mechanical, can become a DETERMINISTIC SCRIPT (in-scope guarantee)
--   direct effect -> mission   : GOAL-POTENCY, stays PROBABILISTIC (the world is in the loop)
--
-- The edge carries THREE INDEPENDENT WEIGHTS (three queryable dials):
--   formality_weight   — epistemic confidence in the causality (grows from interventional evidence)
--   strictness_weight  — how hard it binds behavior: advisory / soft-constraint / hard-guardrail
--   directness_weight  — judgment -> structured predicate -> executable script (action determinism)
--
-- Executor slot: primarily A (actuator). B (verifier) / C (constraint) only when warranted.
--
-- Surprise-driven learning: each edge carries a predicted_certainty; actual vs predicted = surprise.
-- (Surprise resolution + gated weight update is a LATER slice — this migration only stores the
--  prediction bookkeeping. planning_check.py is RECORD-ONLY.)
--
-- JEPA enrichment (BB #829): the planning prediction records a FULL outcome representation,
-- not just a certainty scalar: which principles will be supported / challenged, which edges
-- will be stressed. One prediction row per plan.

BEGIN;

SET search_path = public, ag_catalog, "$user";

-- ---------------------------------------------------------------------------
-- causal_edge — one causal claim, two linked claims, three weights, executor slot.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS causal_edge (
    edge_id             bigserial PRIMARY KEY,

    -- the two linked claims
    source_action       text        NOT NULL,          -- action -> direct effect (mechanical, scriptable)
    direct_effect       text        NOT NULL,          -- the guaranteed-in-scope direct outcome
    mission_measure     text        NOT NULL,          -- direct effect -> mission measure (probabilistic goal-potency)

    -- three independent weights (each a queryable dial, 0..1)
    formality_weight    real        NOT NULL DEFAULT 0.5 CHECK (formality_weight  BETWEEN 0 AND 1),
    strictness_weight   real        NOT NULL DEFAULT 0.5 CHECK (strictness_weight BETWEEN 0 AND 1),
    directness_weight   real        NOT NULL DEFAULT 0.5 CHECK (directness_weight BETWEEN 0 AND 1),

    -- executor slot: A = actuator (default), B = verifier, C = constraint
    executor_slot       char(1)     NOT NULL DEFAULT 'A'
                        CHECK (executor_slot IN ('A','B','C')),

    -- surprise fields
    predicted_certainty real        CHECK (predicted_certainty BETWEEN 0 AND 1),

    -- bookkeeping per edge (BB #746)
    scope_conditions    text,                          -- under what conditions this edge holds
    evidence_run_ids    bigint[]    NOT NULL DEFAULT '{}',  -- runs that produced interventional evidence
    support_count       integer     NOT NULL DEFAULT 0,     -- supporting observations
    falsified_by        bigint,                         -- run that falsified this edge (NULL = not falsified)

    last_validated      timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- A falsified edge must point at the run that falsified it. Unrepresentable otherwise.
ALTER TABLE causal_edge DROP CONSTRAINT IF EXISTS falsified_edge_has_evidence;
ALTER TABLE causal_edge ADD CONSTRAINT falsified_edge_has_evidence CHECK (
    falsified_by IS NULL OR falsified_by > 0
);

CREATE INDEX IF NOT EXISTS causal_edge_executor_idx  ON causal_edge (executor_slot);
CREATE INDEX IF NOT EXISTS causal_edge_falsified_idx ON causal_edge (falsified_by) WHERE falsified_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS causal_edge_strict_idx    ON causal_edge (strictness_weight DESC);

-- ---------------------------------------------------------------------------
-- planning_prediction — RECORD-ONLY. One prediction per plan.
--
-- JEPA enrichment (#829): full outcome representation, not just a certainty scalar.
-- actual_outcome + surprise_level are filled LATER (on outcome); the planning check
-- only WRITES the predicted_* columns. Nothing here gates or redirects a plan.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning_prediction (
    prediction_id                       bigserial PRIMARY KEY,
    edge_id                             bigint      NOT NULL REFERENCES causal_edge(edge_id) ON DELETE CASCADE,
    plan_id                             text        NOT NULL,          -- opaque plan identifier (not a FK: plans are not rows yet)

    -- full outcome representation (BB #829 REC 2)
    predicted_principles_supported      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    predicted_principles_challenged     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    predicted_edges_stressed            jsonb       NOT NULL DEFAULT '[]'::jsonb,

    predicted_certainty                 real        CHECK (predicted_certainty BETWEEN 0 AND 1),

    -- filled later, on outcome. NULL until then = prediction not yet resolved.
    actual_outcome                      text,
    surprise_level                      real        CHECK (surprise_level BETWEEN 0 AND 1),

    recorded_at                         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS planning_prediction_plan_idx  ON planning_prediction (plan_id);
CREATE INDEX IF NOT EXISTS planning_prediction_edge_idx  ON planning_prediction (edge_id);
CREATE INDEX IF NOT EXISTS planning_prediction_unresolved_idx
    ON planning_prediction (plan_id) WHERE actual_outcome IS NULL;

COMMIT;
