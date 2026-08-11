"""The AGE graph mirror is an OPTIONAL, best-effort enrichment (schema/000_extensions.sql):
a mirror of Work/Run/Learning rows that are ALREADY durable in the relational tables, written
only when datastore.graph == 'age'. So a failure writing that mirror must be NON-FATAL — it may
never propagate into the DECIDE cycle.

The live incident (verified): the loop runs as role aq_loop, which cannot `LOAD 'age'`
(AGE is not in shared_preload_libraries and aq_loop is not a superuser), so graph_link raised
psycopg2 InsufficientPrivilege. That failure (a) propagated out of finish_pending_reflection into
cycle(), and (b) POISONED the caller's transaction — in Postgres one failed statement aborts the
whole tx, so the reflection-finish COMMIT silently degraded to ROLLBACK and threw away the
learning. Run #6's pending reflection could never finish, so the loop retried it every cycle,
never wrote a clean heartbeat, and made zero progress while its process stayed up.

These tests are RED-FIRST: they fail against a graph_link that lets the mirror failure escape (or
that catches it without rolling back the poisoned sub-transaction), and pass once the mirror is
run inside a SAVEPOINT that is rolled back on any failure.
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager

from runner.db import Db

from tests.test_loop_approval_execution import FakeDb, FakeExecutor, instance
from runner.loop import Loop
from runner import prompts


class SimulatedInsufficientPrivilege(Exception):
    """Stands in for psycopg2.errors.InsufficientPrivilege raised by `LOAD 'age'` under aq_loop
    (SQLSTATE 42501 / "access to library \"age\" is not allowed"). graph_link catches broadly,
    so the exact class is immaterial; the behaviour we pin is that ANY failure fail-softs."""


class SimulatedInFailedTransaction(Exception):
    """Postgres: 'current transaction is aborted, commands ignored until end of transaction
    block'. Raised for any statement issued on an aborted tx except ROLLBACK TO SAVEPOINT."""


class FakePgCursor:
    """A cursor that faithfully models Postgres aborted-transaction + SAVEPOINT semantics, so a
    fix that merely swallows the exception (without un-poisoning the tx) is still caught red.

    - A failed statement aborts the whole transaction.
    - While aborted, every statement raises InFailedTransaction EXCEPT `ROLLBACK TO SAVEPOINT`,
      which is the one command that clears the aborted state back to the savepoint.
    - `LOAD 'age'` is configured to raise (the live repro) unless load_error is None.
    """

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        up = " ".join(sql.split()).upper()
        self.conn.statements.append(up)

        if up.startswith("ROLLBACK TO SAVEPOINT"):
            # Allowed even when aborted — this is what un-poisons the tx.
            self.conn.aborted = False
            self.conn.rolled_back_to_savepoint = True
            return

        if self.conn.aborted:
            raise SimulatedInFailedTransaction(
                "current transaction is aborted, commands ignored until end of transaction block")

        if up.startswith("SAVEPOINT"):
            self.conn.savepoints.append(up)
            return
        if up.startswith("RELEASE SAVEPOINT"):
            return

        if "LOAD 'AGE'" in up:
            self.conn.load_attempted = True
            if self.conn.load_error is not None:
                self.conn.aborted = True
                raise self.conn.load_error
            return  # AGE is available in this simulated cluster

        if "CYPHER(" in up:
            self.conn.cypher_executed = True
            return

        # Any ordinary statement (e.g. the learning INSERT, or a post-mirror SELECT).
        return


class FakePgConn:
    def __init__(self, load_error=None):
        self.load_error = load_error
        self.aborted = False
        self.committed = False
        self.rolled_back = False
        self.rolled_back_to_savepoint = False
        self.load_attempted = False
        self.cypher_executed = False
        self.statements = []
        self.savepoints = []

    def cursor(self):
        return FakePgCursor(self)

    def commit(self):
        # Postgres: committing an ABORTED transaction is a rollback. This is exactly how the
        # learning got silently discarded before the fix.
        if self.aborted:
            self.rolled_back = True
        else:
            self.committed = True


def _db(graph="age"):
    db = Db.__new__(Db)  # bypass __init__ (no live postgres needed)
    db.graph = graph
    return db


class GraphLinkFailSoftTests(unittest.TestCase):
    def test_load_age_failure_does_not_raise_and_does_not_poison_the_tx(self):
        """The core fix. LOAD 'age' fails (the live aq_loop repro). graph_link must:
        return without raising, leave the caller's transaction healthy (not aborted), and let
        the learning the caller already wrote COMMIT rather than silently roll back."""
        conn = FakePgConn(load_error=SimulatedInsufficientPrivilege(
            'access to library "age" is not allowed'))
        cur = conn.cursor()
        # The caller (finish_pending_reflection) has already written the learning on this cursor.
        cur.execute("INSERT INTO learnings (run_id, insight) VALUES (6, 'x')")

        _db().graph_link(cur, run_id=6, work_id=10, learning_id=701)  # must NOT raise

        self.assertTrue(conn.load_attempted, "the mirror write was actually attempted")
        self.assertFalse(conn.aborted, "the outer tx must be un-poisoned after the mirror fails")
        self.assertTrue(conn.rolled_back_to_savepoint, "failure must roll back the mirror savepoint")
        # The tx is still usable — the caller can keep working on it.
        cur.execute("SELECT 1")  # would raise InFailedTransaction if still poisoned
        conn.commit()
        self.assertTrue(conn.committed, "the learning commit must succeed")
        self.assertFalse(conn.rolled_back, "the learning must NOT be silently discarded")
        self.assertFalse(conn.cypher_executed, "no mirror row is written when LOAD fails")

    def test_non_age_graph_mode_returns_immediately_untouched(self):
        """graph: none (or any non-age mode) short-circuits before touching the cursor."""
        conn = FakePgConn()
        cur = conn.cursor()
        _db(graph="none").graph_link(cur, run_id=6, work_id=10, learning_id=701)
        self.assertEqual(conn.statements, [], "no SQL issued when graph mode != 'age'")

    def test_age_available_path_still_writes_the_mirror(self):
        """Behaviour preserved: when LOAD 'age' succeeds, the cypher MERGE is still executed and
        the savepoint is released cleanly. Only the FAILURE path changed."""
        conn = FakePgConn(load_error=None)  # AGE loads fine
        cur = conn.cursor()
        _db().graph_link(cur, run_id=6, work_id=10, learning_id=701)
        self.assertTrue(conn.load_attempted)
        self.assertTrue(conn.cypher_executed, "the mirror MERGE is still written on the happy path")
        self.assertFalse(conn.aborted)
        self.assertFalse(conn.rolled_back_to_savepoint, "no rollback on the happy path")
        conn.commit()
        self.assertTrue(conn.committed)


class PoisonTxDb(FakeDb):
    """FakeDb whose transaction is a realistic poisoning Postgres connection, wired to the REAL
    Db.graph_link so the loop-level test exercises the actual fail-soft code path."""

    def __init__(self, approved_row=None):
        super().__init__(approved_row)
        self.graph = "age"
        self.pg = FakePgConn(load_error=SimulatedInsufficientPrivilege(
            'access to library "age" is not allowed'))

    @contextmanager
    def tx(self):
        cur = self.pg.cursor()
        yield cur
        self.pg.commit()

    def graph_link(self, cur, run_id, work_id, learning_id):
        # Use the production implementation under test (FakeDb otherwise stubs it out).
        return Db.graph_link(self, cur, run_id, work_id, learning_id)


class CycleWithFailingGraphLinkTests(unittest.TestCase):
    def _pending_reflection(self):
        return {
            "id": 6, "work_id": 10, "summary": "acted work", "rationale": "why",
            "outcome": "did the thing", "succeeded": True, "evidence": "artifact",
        }

    def test_finish_pending_reflection_completes_despite_failing_mirror(self):
        """The stuck-run-#6 scenario. finish_pending_reflection must write the learning and clear
        the pending reflection even though graph_link's mirror write fails."""
        db = PoisonTxDb()
        db.pending_after_act = self._pending_reflection()
        loop = Loop(instance(), db, FakeExecutor())

        loop.finish_pending_reflection()  # must NOT raise

        self.assertEqual(len(db.learned), 1, "the learning was written")
        self.assertEqual(len(db.completed), 1, "the run was completed")
        self.assertIsNone(db.pending_after_act, "the pending reflection is cleared, not retried")
        self.assertTrue(db.pg.committed, "the reflection-finish tx committed (learning kept)")
        self.assertFalse(db.pg.rolled_back)

    def test_full_cycle_returns_normally_when_graph_link_fails(self):
        """cycle() calls finish_pending_reflection early. Pre-fix the mirror failure propagated and
        cycle() raised; forever() would then never beat the heartbeat. Post-fix cycle() returns
        normally (DECIDE do_nothing) and the loop makes progress past run #6."""
        db = PoisonTxDb()
        db.pending_after_act = self._pending_reflection()
        loop = Loop(instance(), db, FakeExecutor())

        result = loop.cycle()  # must NOT raise

        self.assertIsNone(result, "nothing new to do this cycle; DECIDE chose do_nothing")
        self.assertEqual(len(db.learned), 1, "run #6's reflection finished")
        self.assertIn(prompts.DECIDE_SCHEMA, loop.ex.schemas,
                      "the cycle proceeded past the reflection into DECIDE")


if __name__ == "__main__":
    unittest.main()
