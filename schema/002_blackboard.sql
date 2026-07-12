-- The blackboard: shared memory and comms for MORE THAN ONE agent.
--
-- Everything in 001_init is a single loop talking to itself. That is enough for one agent with
-- one mission, and it is not enough for anything else:
--
--   * a SECOND agent (a reviewer, a specialist, a parallel worker) cannot see what the first
--     one learned — so it repeats its mistakes,
--   * work cannot be HANDED OFF, only done,
--   * decisions get re-litigated every session because nothing remembers that they were settled,
--   * and instances cannot share what generalises, which is the whole divergent-evolution bet.
--
-- Deliberately NOT tmux, NOT a socket, NOT a message queue daemon. Postgres is already in the
-- box, and a database-backed bus works on native Windows, survives a restart, and is auditable
-- afterwards. A comms channel you cannot read the history of is a comms channel that will lie to
-- you about what was said.

BEGIN;

-- ---------------------------------------------------------------------------
-- NOTES — the shared board. What any agent knows and every agent can read.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bb_notes (
  id          bigserial PRIMARY KEY,
  created_at  timestamptz NOT NULL DEFAULT now(),
  author      text        NOT NULL,          -- which agent wrote it
  kind        text        NOT NULL DEFAULT 'note'
              CHECK (kind IN ('note','decision','handoff','broadcast','question')),
  title       text        NOT NULL,
  body        text        NOT NULL,
  -- A DECISION that is recorded is a decision that stops being re-argued every session.
  -- This is most of the value: not the note, the not-relitigating.
  supersedes  bigint      REFERENCES bb_notes(id),
  tags        text[]      NOT NULL DEFAULT '{}',
  -- link back to the loop, so a note can point at the run that produced it
  run_id      bigint      REFERENCES runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS bb_notes_kind_idx ON bb_notes (kind, created_at DESC);
CREATE INDEX IF NOT EXISTS bb_notes_tags_idx ON bb_notes USING gin (tags);
-- full-text search: agents ask "what do we know about X", not "give me note 47"
CREATE INDEX IF NOT EXISTS bb_notes_fts_idx ON bb_notes
  USING gin (to_tsvector('english', title || ' ' || body));

-- ---------------------------------------------------------------------------
-- MESSAGES — the bus. Agent to agent, durable, replayable.
--
-- delivered_at is the ONLY thing that means delivered. A message that was "sent" is a message
-- that might be sitting in a dead process's memory; a row with delivered_at set is a message
-- that was actually picked up. We learned this the expensive way elsewhere: HTTP 200 is not
-- delivery.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bb_messages (
  id            bigserial PRIMARY KEY,
  created_at    timestamptz NOT NULL DEFAULT now(),
  sender        text        NOT NULL,
  recipient     text        NOT NULL,        -- an agent name, or '*' for broadcast
  subject       text        NOT NULL,
  body          text        NOT NULL,
  in_reply_to   bigint      REFERENCES bb_messages(id),
  delivered_at  timestamptz,                 -- set when the recipient actually READS it
  handled_at    timestamptz                  -- set when it actually DID something about it
);

CREATE INDEX IF NOT EXISTS bb_messages_inbox_idx
  ON bb_messages (recipient, created_at DESC) WHERE delivered_at IS NULL;

COMMIT;
