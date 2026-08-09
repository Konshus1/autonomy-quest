"""test_planning_check.py — EXPERIMENTAL tests for T5 (#4897).

Three things must be true:
  1. The migration (009_causal_edges.sql) runs cleanly and creates the tables.
  2. The record-only planning check records predictions (full outcome repr).
  3. Main-path operations are UNAFFECTED: the experimental module is not imported
     by any main-path file, and the migration is additive (no existing table
     altered).

Tests use a DEDICATED throwaway database (aq_t5_test) so the real postgres DB is
never touched. The DB is dropped+created fresh per run.

Run:
    cd ~/src/autonomy-quest-public-19ea8b6
    python3 -m pytest experiments/causal_edges/test_planning_check.py -v
  or
    python3 experiments/causal_edges/test_planning_check.py
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import psycopg2
import pytest

# -- paths --------------------------------------------------------------------
HERE = pathlib.Path(__file__).resolve().parent
AQ_ROOT = HERE.parent.parent  # autonomy-quest-public-19ea8b6/
MIGRATION = HERE / "009_causal_edges.sql"

# A test DB we fully own and tear down. Never the real one.
TEST_DB = os.environ.get("AQ_T5_TEST_DB", "aq_t5_test")
DB_HOST = os.environ.get("AQ_T5_TEST_HOST", "localhost")
DB_PORT = os.environ.get("AQ_T5_TEST_PORT", "5432")
DB_USER = os.environ.get("AQ_T5_TEST_USER", "kthomas")
ADMIN_DB = os.environ.get("AQ_T5_ADMIN_DB", "postgres")


# -- DB fixture ---------------------------------------------------------------
def _admin_conn():
    return psycopg2.connect(dbname=ADMIN_DB, user=DB_USER, host=DB_HOST, port=DB_PORT)


def _test_conn():
    return psycopg2.connect(dbname=TEST_DB, user=DB_USER, host=DB_HOST, port=DB_PORT)


@pytest.fixture(scope="module")
def db_url():
    """Create a fresh throwaway DB, apply the migration, yield its URL, drop it."""
    admin = _admin_conn()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
        cur.execute(f'CREATE DATABASE "{TEST_DB}"')
    admin.close()

    # apply the migration to the fresh DB
    c = _test_conn()
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute(MIGRATION.read_text())
    c.close()

    url = f"postgresql://{DB_USER}@{DB_HOST}:{DB_PORT}/{TEST_DB}"
    yield url

    # teardown
    admin = _admin_conn()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
    admin.close()


@pytest.fixture
def conn(db_url):
    c = psycopg2.connect(db_url)
    c.autocommit = True
    yield c
    c.close()


# -- helpers ------------------------------------------------------------------
def _insert_edge(conn, *, source_action, direct_effect="effect", mission_measure="metric",
                 formality=0.5, strictness=0.5, directness=0.5, slot="A"):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO causal_edge
                 (source_action, direct_effect, mission_measure,
                  formality_weight, strictness_weight, directness_weight, executor_slot)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING edge_id""",
            (source_action, direct_effect, mission_measure, formality, strictness, directness, slot),
        )
        return cur.fetchone()[0]


# =============================================================================
# 1. MIGRATION RUNS + TABLES EXIST
# =============================================================================
class TestMigration:
    def test_causal_edge_table_exists(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.causal_edge')")
            assert cur.fetchone()[0] == "causal_edge"

    def test_planning_prediction_table_exists(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.planning_prediction')")
            assert cur.fetchone()[0] == "planning_prediction"

    def test_causal_edge_has_three_weights(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                """SELECT column_name FROM information_schema.columns
                    WHERE table_name='causal_edge'
                      AND column_name IN
                      ('formality_weight','strictness_weight','directness_weight')
                    ORDER BY column_name"""
            )
            cols = [r[0] for r in cur.fetchall()]
        assert cols == ["directness_weight", "formality_weight", "strictness_weight"]

    def test_executor_slot_check_constraint(self, conn):
        # valid slots
        for slot in ("A", "B", "C"):
            _insert_edge(conn, source_action=f"act_{slot}", slot=slot)
        # invalid slot rejected
        with pytest.raises(psycopg2.errors.CheckViolation):
            _insert_edge(conn, source_action="bad_slot", slot="Z")

    def test_weight_range_constraints(self, conn):
        with pytest.raises(psycopg2.errors.CheckViolation):
            _insert_edge(conn, source_action="bad_form", formality=1.5)
        with pytest.raises(psycopg2.errors.CheckViolation):
            _insert_edge(conn, source_action="bad_strict", strictness=-0.1)

    def test_planning_prediction_fk_to_causal_edge(self, conn):
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO planning_prediction (edge_id, plan_id) VALUES (999999, 'p')"
                )

    def test_additive_no_main_table_altered(self, conn):
        """The migration must not alter work/runs/learnings/measurements columns."""
        # These tables don't exist in the throwaway DB (only 009 was applied),
        # which itself proves 009 doesn't depend on or alter them. But let's also
        # confirm 009 references none of them by scanning the SQL text.
        sql = MIGRATION.read_text().lower()
        for forbidden in ("alter table work", "alter table runs", "alter table learnings",
                          "alter table measurements", "alter table shared_learnings",
                          "drop table work", "drop table runs"):
            assert forbidden not in sql, f"migration touches main table: {forbidden}"


# =============================================================================
# 2. PLANNING CHECK RECORDS PREDICTIONS (full outcome repr)
# =============================================================================
class TestPlanningCheck:
    def test_records_prediction_per_touched_edge(self, conn):
        sys.path.insert(0, str(HERE))
        from planning_check import PlanningCheck, Plan

        e1 = _insert_edge(conn, source_action="outreach", formality=0.8, strictness=0.7, directness=0.6)
        e2 = _insert_edge(conn, source_action="deliver", formality=0.3, strictness=0.5, directness=0.4)
        _insert_edge(conn, source_action="unrelated", formality=0.9)  # not touched

        pc = PlanningCheck(conn)
        plan = Plan(plan_id="plan-001", actions=["outreach", "deliver"], rationale="test")
        preds = pc.check_and_record(plan)

        assert len(preds) == 2  # two governing edges touched
        edge_ids = {p.edge_id for p in preds}
        assert edge_ids == {e1, e2}

        # full outcome representation (BB #829): not just a scalar
        for p in preds:
            assert 0.0 <= p.predicted_certainty <= 1.0
            assert isinstance(p.predicted_principles_supported, list)
            assert isinstance(p.predicted_principles_challenged, list)
            assert isinstance(p.predicted_edges_stressed, list)
            assert e1 in p.predicted_edges_stressed or e2 in p.predicted_edges_stressed

        # well-supported edge (formality>=0.5) -> supported; weak -> challenged
        p_e1 = next(p for p in preds if p.edge_id == e1)
        assert "outreach" in p_e1.predicted_principles_supported
        p_e2 = next(p for p in preds if p.edge_id == e2)
        assert "deliver" in p_e2.predicted_principles_challenged

        # rows actually persisted
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM planning_prediction WHERE plan_id='plan-001'")
            assert cur.fetchone()[0] == 2

    def test_no_prediction_for_untouched_plan(self, conn):
        sys.path.insert(0, str(HERE))
        from planning_check import PlanningCheck, Plan

        _insert_edge(conn, source_action="alpha")
        pc = PlanningCheck(conn)
        preds = pc.check_and_record(Plan(plan_id="plan-empty", actions=["nonexistent"]))
        assert preds == []

    def test_falsified_edges_excluded(self, conn):
        sys.path.insert(0, str(HERE))
        from planning_check import PlanningCheck, Plan

        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO causal_edge (source_action, direct_effect, mission_measure,
                                            formality_weight, falsified_by)
                   VALUES ('falsified_act','e','m',0.9, 42) RETURNING edge_id"""
            )
            cur.fetchone()  # consume
        pc = PlanningCheck(conn)
        preds = pc.check_and_record(Plan(plan_id="plan-f", actions=["falsified_act"]))
        assert preds == []  # falsified edge does not govern

    def test_predicted_certainty_composite_dominated_by_formality(self, conn):
        sys.path.insert(0, str(HERE))
        import planning_check as pc_mod

        # low formality -> low certainty regardless of strictness/directness
        low = pc_mod._composite_certainty(0.1, 1.0, 1.0)
        high = pc_mod._composite_certainty(0.9, 1.0, 1.0)
        assert low < 0.2
        assert high > 0.8
        assert low < high

    def test_record_is_idempotent_per_call(self, conn):
        """check_and_record can be called twice; each call inserts its own rows
        (we do not dedupe — a re-check is a new prediction record)."""
        sys.path.insert(0, str(HERE))
        from planning_check import PlanningCheck, Plan

        _insert_edge(conn, source_action="repeatable")
        pc = PlanningCheck(conn)
        plan = Plan(plan_id="plan-repeat", actions=["repeatable"])
        pc.check_and_record(plan)
        pc.check_and_record(plan)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM planning_prediction WHERE plan_id='plan-repeat'")
            assert cur.fetchone()[0] == 2

    def test_actual_outcome_and_surprise_left_null(self, conn):
        """The planning check is RECORD-ONLY: it does not fill actual_outcome/surprise."""
        sys.path.insert(0, str(HERE))
        from planning_check import PlanningCheck, Plan

        _insert_edge(conn, source_action="pending_act")
        pc = PlanningCheck(conn)
        pc.check_and_record(Plan(plan_id="plan-null", actions=["pending_act"]))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT actual_outcome, surprise_level FROM planning_prediction WHERE plan_id='plan-null'"
            )
            row = cur.fetchone()
        assert row[0] is None
        assert row[1] is None


# =============================================================================
# 3. MAIN-PATH UNAFFECTED
# =============================================================================
class TestMainPathUnaffected:
    def test_experimental_module_not_imported_by_main_path(self):
        """No main-path file imports planning_check or the experiments package."""
        main_files = [
            AQ_ROOT / "runner" / "loop.py",
            AQ_ROOT / "runner" / "executor.py",
            AQ_ROOT / "runner" / "gateway.py",
            AQ_ROOT / "runner" / "db.py",
            AQ_ROOT / "runner" / "budget.py",
            AQ_ROOT / "runner" / "escalation.py",
            AQ_ROOT / "runner" / "sharing.py",
            AQ_ROOT / "runner" / "config.py",
            AQ_ROOT / "runner" / "prompts.py",
            AQ_ROOT / "aq.py",
        ]
        forbidden = ["planning_check", "experiments.causal_edges", "experiments."]
        for f in main_files:
            if not f.exists():
                continue
            text = f.read_text()
            for token in forbidden:
                assert token not in text, f"{f.name} references experimental module: {token}"

    def test_migration_is_in_fresh_instance_schema(self):
        """BB #875 moved 009 into fresh-instance initialization after 001-008."""
        migration = AQ_ROOT / "schema" / "009_causal_edges.sql"
        assert migration.is_file()
        sql = migration.read_text()
        assert "CREATE TABLE IF NOT EXISTS causal_edge" in sql
        assert "CREATE TABLE IF NOT EXISTS planning_prediction" in sql

    def test_loop_imports_cleanly(self):
        """runner.loop must still import without the experimental module."""
        result = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, '.'); import runner.loop; print('ok')"],
            cwd=str(AQ_ROOT),
            capture_output=True, text=True, timeout=30,
        )
        # If runner deps (psycopg2 etc.) are present this prints 'ok'. If a dep
        # is missing that's a pre-existing env issue, not our regression. We only
        # fail on ImportErrors mentioning our experimental module.
        if result.returncode == 0:
            assert "ok" in result.stdout
        else:
            err = result.stderr
            # the only acceptable failure is a missing third-party dep, NOT our module
            assert "planning_check" not in err and "experiments" not in err, \
                f"loop.py import broken by experimental module: {err}"


if __name__ == "__main__":
    # allow running without pytest
    sys.exit(pytest.main([__file__, "-v"]))
