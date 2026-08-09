-- Observed loop-process liveness, separate from progress/cycle evidence.
BEGIN;
CREATE TABLE IF NOT EXISTS loop_runtime (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  pid integer NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  beat_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
