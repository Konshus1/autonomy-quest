-- 016_learning_evidence_provenance.sql — prose reflection is a claim, not verified evidence.
BEGIN;
ALTER TABLE learnings
  ADD COLUMN IF NOT EXISTS evidence_kind text NOT NULL DEFAULT 'actor_claim';
UPDATE learnings SET confidence=0.99
WHERE evidence_kind='actor_claim' AND confidence >= 1.0;
ALTER TABLE learnings DROP CONSTRAINT IF EXISTS learnings_evidence_kind_check;
ALTER TABLE learnings ADD CONSTRAINT learnings_evidence_kind_check
  CHECK (evidence_kind IN ('actor_claim','verified_evidence'));
ALTER TABLE learnings DROP CONSTRAINT IF EXISTS learnings_actor_claim_not_certain;
ALTER TABLE learnings ADD CONSTRAINT learnings_actor_claim_not_certain
  CHECK (evidence_kind='verified_evidence' OR confidence < 1.0);
COMMIT;
