-- CROSS-INSTANCE LEARNING — where divergent evolution actually pays off, and where it can do the
-- most damage if we are careless.
--
-- THE TRAP: a learning's `scope = generalisable` flag is SELF-AUTHORED. The instance that wrote
-- the learning is the same one that graded its own portability. If a receiving instance treats
-- that flag as truth, then one instance's self-certification becomes another instance's
-- EXECUTABLE TRUTH — and a single bad generalisation propagates into every instance that trusts
-- it, where it will be read as prior knowledge and acted on forever.
--
-- So: A GENERALISABLE LABEL IS A PROPOSAL, NOT PROOF.
--
-- Imports are STAGED. A staged learning is inert: it does NOT reach the loop, it does NOT appear
-- in live_learnings(), and it cannot influence a single decision until this instance adjudicates
-- and promotes it. Promotion is local, reversible, and recorded.
BEGIN;

CREATE TABLE IF NOT EXISTS shared_learnings (
  id              bigserial PRIMARY KEY,
  received_at     timestamptz NOT NULL DEFAULT now(),

  -- PROVENANCE. Without it, a bad learning cannot be traced back or recalled, and you cannot ask
  -- the only question that matters: "who says, and on what evidence?"
  origin_instance text        NOT NULL,
  origin_mission  text        NOT NULL,   -- a DIFFERENT mission is the whole point of the claim
  origin_run_id   bigint,
  origin_evidence text        NOT NULL,   -- what actually happened over there
  origin_outcome  text,                   -- the raw outcome, not just their gloss on it

  insight         text        NOT NULL,
  confidence      real        NOT NULL,

  -- THE CAUSAL CLAIM, stated so it can be argued with rather than merely believed.
  applies_when    text        NOT NULL,   -- predicate: when is this claim even relevant here?
  falsified_by    text        NOT NULL,   -- what observation would DISPROVE it? (no falsifier = not a claim)

  -- Adjudication is LOCAL. Another instance's confidence is not this instance's evidence.
  status          text        NOT NULL DEFAULT 'staged'
                  CHECK (status IN ('staged','promoted','rejected','rolled_back')),
  adjudicated_at  timestamptz,
  adjudicated_by  text,
  adjudication    text,                   -- WHY. A promotion with no reason is a rubber stamp.

  -- If promoted, this is the local learning it became — so it can be ROLLED BACK.
  local_learning_id bigint REFERENCES learnings(id) ON DELETE SET NULL,

  UNIQUE (origin_instance, insight)       -- idempotent import; re-importing does not re-stage
);

CREATE INDEX IF NOT EXISTS shared_staged_idx ON shared_learnings (received_at DESC)
  WHERE status = 'staged';

COMMIT;
