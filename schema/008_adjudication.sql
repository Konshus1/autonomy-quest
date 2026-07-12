-- PROMOTION REQUIRES LOCAL EVIDENCE, NOT A GOOD SENTENCE.
--
-- The staging boundary stopped a foreign self-authored 'generalisable' flag from reaching the
-- loop. But promotion itself was authorised by PROSE — a reason string. An eloquent reason is
-- exactly what a plausible-but-wrong claim will attract, so "self-authored promotion" was the
-- last path by which foreign prose could still become executable truth here.
--
-- A reason EXPLAINS a promotion. It must never AUTHORISE one.
--
-- To promote, this instance must show its own work:
--   * the applicability predicate, EVALUATED HERE (does this claim even apply to our mission?)
--   * a NEGATIVE CONTROL that was actually RUN, and what it returned
--   * a pointable evidence reference (a query, a file, a run id — something a human can re-check)
--   * an adjudicator identity, which for a high-confidence claim must not be whoever imported it
BEGIN;

ALTER TABLE shared_learnings ADD COLUMN IF NOT EXISTS imported_by         text;
ALTER TABLE shared_learnings ADD COLUMN IF NOT EXISTS applies_here        boolean;
ALTER TABLE shared_learnings ADD COLUMN IF NOT EXISTS applies_here_how    text;
ALTER TABLE shared_learnings ADD COLUMN IF NOT EXISTS negative_control    text;
ALTER TABLE shared_learnings ADD COLUMN IF NOT EXISTS negative_control_result text;
ALTER TABLE shared_learnings ADD COLUMN IF NOT EXISTS evidence_ref        text;

-- The authorization predicate, in the SCHEMA. A promoted row that lacks local evidence is not
-- merely discouraged — it is UNREPRESENTABLE.
ALTER TABLE shared_learnings DROP CONSTRAINT IF EXISTS promotion_requires_local_evidence;
ALTER TABLE shared_learnings ADD CONSTRAINT promotion_requires_local_evidence CHECK (
  status <> 'promoted' OR (
        applies_here IS TRUE
    AND applies_here_how        IS NOT NULL AND length(trim(applies_here_how))        > 0
    AND negative_control        IS NOT NULL AND length(trim(negative_control))        > 0
    AND negative_control_result IS NOT NULL AND length(trim(negative_control_result)) > 0
    AND evidence_ref            IS NOT NULL AND length(trim(evidence_ref))            > 0
    AND adjudicated_by          IS NOT NULL AND length(trim(adjudicated_by))          > 0
  )
);

COMMIT;
