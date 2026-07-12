-- rate_limited must have a DEADLINE, or "healthy" becomes indefinite limbo.
--
-- Without this, a loop stuck beating rate_limited forever is reported HEALTHY forever: it is not
-- turning, it is not hibernating, and nothing ever says otherwise. "Waiting for the plan to reset"
-- is only a valid state UNTIL the plan resets. After the deadline passes and the loop is still
-- saying rate_limited, something is WRONG and the healthcheck must say so.
--
-- Note every timestamp in this schema is DB-generated (DEFAULT now()) and every comparison is
-- against now() IN THE DATABASE. A client clock is not a clock: a process with a skewed or frozen
-- clock could otherwise hold itself "healthy" indefinitely.
BEGIN;

ALTER TABLE heartbeat ADD COLUMN IF NOT EXISTS retry_until timestamptz;

COMMIT;
