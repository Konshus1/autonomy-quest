-- HIBERNATION STATE — so that stopping is not the same as being abandoned.
--
-- The bug this fixes: hibernate raised, the loop exited, systemd restarted it (Restart=always),
-- it re-read the same streak from the database, hibernated again, and EMAILED AGAIN. Forever.
-- Cost control turned into a notify storm, and nothing anywhere recorded that the system was
-- waiting on a human — so a reply had nothing to resume.
--
-- An unresolved escalation must SURVIVE the stop. Otherwise "we stopped spending" quietly means
-- "we abandoned the mission and nobody was told".
BEGIN;

CREATE TABLE IF NOT EXISTS hibernation (
  id            bigserial PRIMARY KEY,
  hibernated_at timestamptz NOT NULL DEFAULT now(),
  reason        text        NOT NULL,
  unproductive  int         NOT NULL,
  notified_at   timestamptz,          -- the human was told ONCE. Not once per restart.
  -- Resume happens exactly once, and only on a REAL new signal — a human approving parked work,
  -- or a peer sending a message. NOT on elapsed time, and NOT on a reboot: a restart must never
  -- be able to manufacture progress or reset the ladder.
  resume_signal text,
  resumed_at    timestamptz
);

CREATE INDEX IF NOT EXISTS hibernation_open_idx
  ON hibernation (hibernated_at DESC) WHERE resumed_at IS NULL;

COMMIT;
