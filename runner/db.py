"""The durable history. Everything the system knows, and the only thing it trusts.

Design rule running through this file: the database is GROUND TRUTH and memory is not.
Costs are summed from rows, the mission's number is re-read from its real source, and the
record+learn write is one transaction. Nothing important is held in a process that a crash
can reset.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from decimal import Decimal

import psycopg2
import psycopg2.extras

log = logging.getLogger("aq.db")


class MeasureUnreadable(RuntimeError):
    """The mission's number could not be read from its real source.

    We STOP rather than proceed on a stale or assumed value. A loop steering on a number it
    couldn't actually read is worse than a halted loop, because it looks like it's working.
    """


@dataclass
class Work:
    id: int
    kind: str
    summary: str
    rationale: str
    requires_human: bool = False
    reversible: bool = True
    spends_money: bool = False
    touches_human: bool = False
    commits: bool = False


class Db:
    def __init__(self, url: str, graph: str = "age") -> None:
        self.url = url
        self.graph = graph
        self.conn = psycopg2.connect(url)
        self.conn.autocommit = True

    @contextlib.contextmanager
    def tx(self):
        """One transaction. Used to make record+learn atomic — see loop.py invariant 1."""
        self.conn.autocommit = False
        try:
            with self.conn.cursor() as cur:
                yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self.conn.autocommit = True

    def _q(self, sql: str, args=(), one=False):
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, args)
            if cur.description is None:
                return None
            return cur.fetchone() if one else cur.fetchall()

    # -- observe: GROUND TRUTH ------------------------------------------------
    def read_measure(self, measure) -> Decimal:
        """Re-read the mission's number from wherever it actually lives.

        `measure.where` is the query the human gave us in the interview. We run THAT.
        We do not read a cached copy, and we do not ask the model how it thinks it's going.
        """
        try:
            row = self._q(measure.where, one=True)
        except Exception as e:
            raise MeasureUnreadable(
                f"could not read {measure.what!r} from its source: {e}. "
                f"Stopping rather than steering on a number we did not actually read."
            ) from e
        if not row:
            raise MeasureUnreadable(f"{measure.what!r} query returned no rows: {measure.where!r}")
        return Decimal(str(list(row.values())[0]))

    def record_measurement(self, measure, value: Decimal) -> None:
        self._q(
            "INSERT INTO measurements (metric, value, source) VALUES (%s, %s, %s)",
            (measure.what, value, measure.where),
        )

    def measure_trend(self, measure, days: int = 14):
        return self._q(
            "SELECT taken_at, value FROM measurements WHERE metric=%s "
            "AND taken_at > now() - (%s || ' days')::interval ORDER BY taken_at",
            (measure.what, days),
        )

    def live_learnings(self, limit: int = 50):
        """What it believes right now. Superseded beliefs are excluded, not deleted —
        this is what makes cycle 100 different from cycle 1."""
        return self._q(
            "SELECT id, insight, evidence, scope, confidence FROM learnings "
            "WHERE superseded_by IS NULL ORDER BY confidence DESC, created_at DESC LIMIT %s",
            (limit,),
        )

    def open_work(self):
        return self._q("SELECT * FROM work WHERE status IN ('pending','running') ORDER BY created_at")

    def awaiting_human(self):
        return self._q("SELECT * FROM work WHERE status='awaiting_human' ORDER BY created_at")

    def recent_runs(self, limit: int = 10):
        return self._q(
            "SELECT r.id, r.outcome, r.succeeded, r.rolled_back, w.kind, w.summary "
            "FROM runs r JOIN work w ON w.id=r.work_id "
            "WHERE r.completed_at IS NOT NULL ORDER BY r.completed_at DESC LIMIT %s",
            (limit,),
        )

    # -- budget: summed from ROWS, never from a counter ------------------------
    def sum_cost(self, since: str) -> Decimal:
        window = "date_trunc('day', now())" if since == "today" else "date_trunc('month', now())"
        row = self._q(f"SELECT COALESCE(SUM(cost_usd),0) AS c FROM runs WHERE started_at >= {window}", one=True)
        return Decimal(str(row["c"]))

    # -- write ----------------------------------------------------------------
    def create_work(self, kind, summary, rationale, requires_human=False) -> int:
        row = self._q(
            "INSERT INTO work (kind, summary, rationale, requires_human) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            (kind, summary, rationale, requires_human), one=True,
        )
        return row["id"]

    def start_run(self, work_id: int) -> int:
        self._q("UPDATE work SET status='running' WHERE id=%s", (work_id,))
        row = self._q("INSERT INTO runs (work_id) VALUES (%s) RETURNING id", (work_id,), one=True)
        return row["id"]

    def complete_run(self, cur, run_id: int, outcome: str, succeeded: bool, usage,
                     productive: bool = True, evidence: str = "",
                     measure_before=None, measure_after=None, escalation_level: str = "autonomous") -> None:
        cur.execute(
            "UPDATE runs SET completed_at=now(), outcome=%s, succeeded=%s, "
            "cost_usd=%s, tokens_in=%s, tokens_out=%s, "
            "productive=%s, evidence=%s, measure_before=%s, measure_after=%s, escalation_level=%s "
            "WHERE id=%s",
            (outcome, succeeded, usage.cost_usd, usage.tokens_in, usage.tokens_out,
             productive, evidence, measure_before, measure_after, escalation_level, run_id),
        )
        cur.execute("UPDATE work SET status='done' WHERE id=(SELECT work_id FROM runs WHERE id=%s)", (run_id,))

    def write_learning(self, cur, run_id, insight, evidence, scope, confidence) -> int:
        cur.execute(
            "INSERT INTO learnings (run_id, insight, evidence, scope, confidence) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (run_id, insight, evidence, scope, confidence),
        )
        return cur.fetchone()[0]

    def recent_productivity(self, limit: int = 15):
        """Newest first. The escalation ladder reads THIS — the database — not a counter in
        memory. A counter that resets on restart lets a stuck loop hammer forever by crashing."""
        return self._q(
            "SELECT id, coalesce(productive, false) AS productive, completed_at FROM runs "
            "WHERE completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT %s", (limit,))

    def fail_run(self, run_id: int, error: str) -> None:
        """A cycle that died leaves a row saying it died. Loud, not silent."""
        self._q(
            "UPDATE runs SET completed_at=now(), outcome=%s, succeeded=false, error=%s WHERE id=%s",
            (f"FAILED: {error[:400]}", error, run_id),
        )
        self._q("UPDATE work SET status='pending' WHERE id=(SELECT work_id FROM runs WHERE id=%s)", (run_id,))

    def abandon_run(self, run_id: int) -> None:
        """Rate-limited, not failed. Nothing went wrong — the plan is simply used up for now.

        We DELETE the run rather than marking it failed. A rate limit is not an outcome, and
        recording it as one would poison the history the loop learns from: the next cycle would
        "learn" from a failure that never happened. The work returns to pending and gets picked
        up when the plan resets.
        """
        self._q("UPDATE work SET status='pending' WHERE id=(SELECT work_id FROM runs WHERE id=%s)", (run_id,))
        self._q("DELETE FROM runs WHERE id=%s AND completed_at IS NULL", (run_id,))

    def park_for_human(self, work_id: int) -> None:
        self._q("UPDATE work SET status='awaiting_human' WHERE id=%s", (work_id,))

    # -- graph ---------------------------------------------------------------
    def graph_link(self, cur, run_id: int, work_id: int, learning_id: int) -> None:
        """Mirror the cycle into the AGE graph: (Run)-[:EXECUTED]->(Work),
        (Learning)-[:DERIVED_FROM]->(Run). Rows say WHAT happened; the graph says what
        RESEMBLES what, which is how it reasons across its history instead of just
        accumulating it.

        If AGE isn't installed (graph: none in the interview), the relational history is
        still complete and the loop is still whole — the graph is a reasoning aid, not the
        system of record. We skip explicitly rather than failing.
        """
        if self.graph != "age":
            return
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public;")
        cur.execute(
            "SELECT * FROM cypher('aq', $$ "
            "  MERGE (w:Work {id: %(w)s}) MERGE (r:Run {id: %(r)s}) "
            "  MERGE (l:Learning {id: %(l)s}) "
            "  MERGE (r)-[:EXECUTED]->(w) MERGE (l)-[:DERIVED_FROM]->(r) "
            "$$) AS (v agtype);",
            {"w": work_id, "r": run_id, "l": learning_id},
        )
