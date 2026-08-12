"""Intra-instance DURABLE mailbox bus (design §6 steps 5+7, §11 invariants).

DURABLE-FIRST: a message is written to durable storage and COMMITTED before ``publish`` returns.
The stored message is the truth; notification (waking a pane/worker) is best-effort AFTER the commit.
An ephemeral store must never acknowledge a message as durable — so this bus is backed by SQLite on
disk (WAL), and durability is proven by reopening the DB file in a fresh process/handle and still
reading the message.

CURSOR-POLL RECOVERY: every channel has a monotonic per-message sequence. A reader polls FROM A
CURSOR (the last seq it consumed) and receives everything after it. Therefore:

  * a LOST/RESTARTED delivery adapter (tmux pane died, wake never fired) loses NOTHING — the next
    cursor poll re-reads the durable message;
  * a MISSED PUSH is recovered by the same poll; delivery is an optimization, not the channel.

CLOSED SCHEMA: only the handoff kinds in ``ROLE_MESSAGE_KINDS`` may be published. There is
deliberately NO ``command`` / ``shell`` / ``exec`` / ``executable`` kind — a message is a
conversation turn or a verdict, never an instruction to run something. Sender identity is passed in
already SERVER-DERIVED (the caller authenticates via ``RolePrincipalRegistry`` first); the bus
stamps the derived ``sender_role`` and ignores any role a payload names.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# The ONLY message kinds the intra-instance bus accepts (closed schema, design §8.4). Role handoffs
# and verdicts — nothing executable.
ROLE_MESSAGE_KINDS: frozenset[str] = frozenset({
    "role.plan",      # planner -> coder: a proposed plan (evidence/among the conversation)
    "role.code",      # coder -> reviewer: the produced decision/plan under review
    "role.review",    # reviewer -> * : a verdict. ONE EVIDENCE SOURCE, never an authorization.
    "role.evaluate",  # evaluator -> * : an evaluation. Also evidence only.
    "role.result",    # producer -> orchestrator: the final structured decision for the loop
    "role.note",      # free-form conversation turn (no authority)
})

# Hard size ceilings — a role cannot exhaust local storage with one giant message.
MAX_PAYLOAD_BYTES = 256 * 1024
# Bounded retention per channel: the bus is a run-scoped conversation, not an archive.
MAX_MESSAGES_PER_CHANNEL = 10_000


class MailboxError(Exception):
    """Base for a mailbox-bus rejection."""


@dataclass(frozen=True)
class Message:
    seq: int
    channel: str
    kind: str
    sender_role: str
    payload: dict
    ts: float


class MailboxBus:
    """A durable, cursor-polled, intra-instance mailbox. One SQLite file per instance/run.

    The bus does NOT authenticate — the caller derives the sender principal via
    ``RolePrincipalRegistry`` and passes the (already trusted) ``sender_role`` and authorized
    ``channel``. This separation keeps the store dumb and the identity server-derived.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        # check_same_thread=False so a worker thread may poll; WAL for durable concurrent readers.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")  # durability over speed: commit hits disk
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS mailbox ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " channel TEXT NOT NULL,"
            " kind TEXT NOT NULL,"
            " sender_role TEXT NOT NULL,"
            " payload TEXT NOT NULL,"
            " ts REAL NOT NULL)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS mailbox_channel_seq ON mailbox(channel, seq)")

    # -- write ---------------------------------------------------------------
    def publish(self, *, channel: str, kind: str, sender_role: str, payload: dict) -> int:
        """Durably store a message and return its monotonic ``seq``. Commits BEFORE returning.

        Rejects (fail-closed) an unknown kind, an oversized payload, or a channel that has hit its
        retention bound. No notification happens here — delivery is a separate, best-effort step.
        """
        if kind not in ROLE_MESSAGE_KINDS:
            raise MailboxError(
                f"kind {kind!r} is not an allowed role message kind {sorted(ROLE_MESSAGE_KINDS)}; "
                f"there is no command/shell/executable kind")
        if not isinstance(payload, dict):
            raise MailboxError("payload must be a JSON object")
        blob = json.dumps(payload, separators=(",", ":"))
        if len(blob.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise MailboxError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
        count = self._conn.execute(
            "SELECT COUNT(*) FROM mailbox WHERE channel=?", (channel,)).fetchone()[0]
        if count >= MAX_MESSAGES_PER_CHANNEL:
            raise MailboxError(
                f"channel {channel!r} reached its retention bound {MAX_MESSAGES_PER_CHANNEL}")
        cur = self._conn.execute(
            "INSERT INTO mailbox(channel, kind, sender_role, payload, ts) VALUES (?,?,?,?,?)",
            (channel, kind, sender_role, blob, time.time()),
        )
        # isolation_level=None => autocommit; PRAGMA synchronous=FULL => the row is on disk now.
        return int(cur.lastrowid)

    # -- read ----------------------------------------------------------------
    def poll(self, *, channel: str, cursor: int = 0, limit: int = 256) -> tuple[list[Message], int]:
        """Return messages on ``channel`` with ``seq > cursor``, plus the new cursor.

        A reader keeps its own cursor and calls this on wake. Because the messages are durable and
        the cursor is monotonic, this is the RECOVERY path: whatever was missed by a lost push is
        returned here. The returned cursor is the max seq seen (or the old cursor if none).
        """
        rows = self._conn.execute(
            "SELECT seq, channel, kind, sender_role, payload, ts FROM mailbox "
            "WHERE channel=? AND seq>? ORDER BY seq ASC LIMIT ?",
            (channel, cursor, limit),
        ).fetchall()
        msgs = [
            Message(seq=r[0], channel=r[1], kind=r[2], sender_role=r[3],
                    payload=json.loads(r[4]), ts=r[5])
            for r in rows
        ]
        new_cursor = msgs[-1].seq if msgs else cursor
        return msgs, new_cursor

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
