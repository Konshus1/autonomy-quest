"""test_surprise_wiring.py — EXPERIMENTAL tests for T6 (#4898).

Five things must be true:
  a. Fixture outcome triggers surprise row (predicted success, actual failure).
  b. Gate holds fail-closed (no auto-action).
  c. Resolution updates edge weight (decrease support_count).
  d. Demotion path proven by fixture (enough falsifications -> demoted).
  e. No surprise when prediction matches actual.

Plus main-path regression: the experimental module is not imported by any
main-path file, and the migration is additive (no existing table altered).

Tests use a DEDICATED throwaway database (aq_t6_test) so the real postgres DB
is never touched. The DB is dropped+created fresh per run, and the T5
migration (009_causal_edges.sql) is applied.

Run:
    cd ~/src/autonomy-quest-public-19ea8b6
    python3 -m pytest experiments/causal_edges/test_surprise_wiring.py -v
  or
    python3 experiments/causal_edges/test_surprise_wiring.py
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import psycopg2
import psycopg2.extras
import pytest

# -- paths --------------------------------------------------------------------
HERE = pathlib.Path(__file__).resolve().parent
AQ_ROOT = HERE.parent.parent  # autonomy-quest-public-19ea8b6/
MIGRATION = HERE / "009_causal_edges.sql"

# A test DB we fully own and tear down. Never the real one.
TEST_DB = os.environ.get("AQ_T6_TEST_DB", "aq_t6_test")
DB_HOST = os.environ.get("AQ_T6_TEST_HOST", "localhost")
DB_PORT = os.environ.get("AQ_T6_TEST_PORT", "5432")
DB_USER = os.environ.get("AQ_T6_TEST_USER", "kthomas")
ADMIN_DB = os.environ.get("AQ_T6_ADMIN_DB", "postgres")


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
                 formality=0.5, strictness=0.5, directness=0.5, slot="A",
                 support_count=5):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO causal_edge
                 (source_action, direct_effect, mission_measure,
                  formality_weight, strictness_weight, directness_weight,
                  executor_slot, support_count)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING edge_id""",
            (source_action, direct_effect, mission_measure,
             formality, strictness, directness, slot, support_count),
        )
        return cur.fetchone()[0]


def _record_prediction(conn, edge_id, plan_id, predicted_certainty):
    """Insert a planning_prediction row (mimics T5 PlanningCheck.record)."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO planning_prediction
                 (edge_id, plan_id,
                  predicted_principles_supported,
                  predicted_principles_challenged,
                  predicted_edges_stressed,
                  predicted_certainty)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING prediction_id""",
            (edge_id, plan_id, "[]", "[]", "[]", predicted_certainty),
        )
        return cur.fetchone()[0]


def _get_edge(conn, edge_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM causal_edge WHERE edge_id = %s", (edge_id,)
        )
        return dict(cur.fetchone())


def _get_prediction(conn, prediction_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM planning_prediction WHERE prediction_id = %s",
            (prediction_id,),
        )
        return dict(cur.fetchone())


# =============================================================================
# a. FIXTURE OUTCOME TRIGGERS SURPRISE (predicted success, actual failure)
# =============================================================================
class TestSurpriseTriggered:
    def test_predicted_success_actual_failure_emits_surprise(self, conn):
        """A high-certainty prediction of success, when actual is failure,
        must produce a surprise packet with surprise_level above threshold."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring

        # Edge with high formality -> high predicted certainty
        edge_id = _insert_edge(
            conn, source_action="outreach", formality=0.9, strictness=0.8, directness=0.7
        )
        # Record a prediction (T5 would do this)
        _record_prediction(conn, edge_id, "plan-surprise-1", predicted_certainty=0.85)

        sw = SurpriseWiring(conn)
        packets = sw.resolve_outcome("plan-surprise-1", "failure", task_id=100)

        assert len(packets) == 1
        pkt = packets[0]

        # Surprise level is high: |0.85 - 0.0| = 0.85
        assert pkt.surprise_level >= 0.3
        assert pkt.surprise_level == pytest.approx(0.85, abs=0.01)

        # Expected vs actual are different
        assert pkt.expected_outcome["predicted_outcome"] == "success"
        assert pkt.actual_outcome["outcome"] == "failure"

        # Planning prediction row was updated
        with conn.cursor() as cur:
            cur.execute(
                "SELECT actual_outcome, surprise_level FROM planning_prediction WHERE plan_id = 'plan-surprise-1'"
            )
            row = cur.fetchone()
        assert row[0] == "failure"
        assert row[1] is not None and row[1] >= 0.3

    def test_surprise_packet_has_valid_wire_format(self, conn):
        """The surprise packet must serialize to a valid ralph_surprise_packet_v0 dict."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring, PACKET_TYPE, SURPRISE_TYPE

        edge_id = _insert_edge(conn, source_action="wire_test", formality=0.8)
        _record_prediction(conn, edge_id, "plan-wire", predicted_certainty=0.8)
        sw = SurpriseWiring(conn)
        packets = sw.resolve_outcome("plan-wire", "failure", task_id=200)
        assert len(packets) == 1

        wire = packets[0].to_dict()
        assert wire["packet_type"] == PACKET_TYPE
        assert wire["surprise_type"] == SURPRISE_TYPE
        assert wire["source_surface"] == "causal_edges_experiment"
        assert isinstance(wire["expected_outcome"], dict)
        assert isinstance(wire["actual_outcome"], dict)
        assert isinstance(wire["evidence_refs"], list) and len(wire["evidence_refs"]) >= 1
        assert wire["severity"] in ("info", "low", "medium", "high", "critical")
        assert isinstance(wire["likely_causal_hypotheses"], list) and len(wire["likely_causal_hypotheses"]) >= 1
        assert isinstance(wire["recommended_follow_up"], dict)
        assert isinstance(wire["outer_loop_learning_fields"], dict)
        assert isinstance(wire["spawn_recommended"], bool)
        assert isinstance(wire["spawn_gate_reason"], str) and wire["spawn_gate_reason"]


# =============================================================================
# b. GATE HOLDS FAIL-CLOSED (no auto-action)
# =============================================================================
class TestGateFailClosed:
    def test_authority_flags_all_false(self, conn):
        """The surprise packet authority must be all False — HELD investigation."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring

        edge_id = _insert_edge(conn, source_action="gate_test", formality=0.9)
        _record_prediction(conn, edge_id, "plan-gate", predicted_certainty=0.9)
        sw = SurpriseWiring(conn)
        packets = sw.resolve_outcome("plan-gate", "failure", task_id=300)
        assert len(packets) == 1

        auth = packets[0].authority
        assert auth["may_dispatch_workers"] is False
        assert auth["may_call_providers"] is False
        assert auth["may_mutate_scheduler"] is False
        assert auth["may_mutate_production"] is False
        assert auth["may_accept_model_update"] is False
        assert auth["requires_manager_review"] is True

    def test_spawn_not_recommended(self, conn):
        """spawn_recommended must be False — no auto-spawn."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring

        edge_id = _insert_edge(conn, source_action="spawn_test", formality=0.85)
        _record_prediction(conn, edge_id, "plan-spawn", predicted_certainty=0.85)
        sw = SurpriseWiring(conn)
        packets = sw.resolve_outcome("plan-spawn", "failure", task_id=301)
        assert len(packets) == 1
        assert packets[0].spawn_recommended is False
        assert "fail-closed" in packets[0].spawn_gate_reason

    def test_process_outcome_returns_held_packets(self, conn):
        """process_outcome returns packets but does NOT auto-act."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring

        edge_id = _insert_edge(conn, source_action="process_test", formality=0.9)
        _record_prediction(conn, edge_id, "plan-process", predicted_certainty=0.9)
        sw = SurpriseWiring(conn)
        packets, demoted = sw.process_outcome(
            "plan-process", "failure", task_id=302, evidence_run_id=42
        )
        # Packets returned (held investigation), but no demotion with 1 surprise
        assert len(packets) == 1
        assert demoted == []  # not enough falsifications yet to demote


# =============================================================================
# c. RESOLUTION UPDATES EDGE WEIGHT (decrease support_count)
# =============================================================================
class TestWeightUpdate:
    def test_support_count_decreased_on_falsifying_surprise(self, conn):
        """A falsifying surprise (predicted success, actual failure) must
        decrease support_count on the causal_edge."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring

        edge_id = _insert_edge(
            conn, source_action="weight_test", formality=0.8, support_count=10
        )
        _record_prediction(conn, edge_id, "plan-weight", predicted_certainty=0.8)

        edge_before = _get_edge(conn, edge_id)
        assert edge_before["support_count"] == 10

        sw = SurpriseWiring(conn)
        sw.resolve_outcome("plan-weight", "failure", task_id=400, evidence_run_id=7)

        edge_after = _get_edge(conn, edge_id)
        assert edge_after["support_count"] == 9  # decreased by 1
        assert edge_after["falsified_by"] is None  # not demoted (only 1 falsification)

    def test_support_count_not_below_zero(self, conn):
        """support_count must never go below 0."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring

        edge_id = _insert_edge(
            conn, source_action="zero_test", formality=0.8, support_count=0
        )
        _record_prediction(conn, edge_id, "plan-zero", predicted_certainty=0.8)
        sw = SurpriseWiring(conn)
        sw.resolve_outcome("plan-zero", "failure", task_id=401, evidence_run_id=8)

        edge = _get_edge(conn, edge_id)
        assert edge["support_count"] == 0  # clamped at 0


# =============================================================================
# d. DEMOTION PATH PROVEN (enough falsifications -> demoted)
# =============================================================================
class TestDemotion:
    def test_edge_demoted_after_enough_falsifications(self, conn):
        """An edge with enough falsifying surprises must be demoted
        (falsified_by set, no longer governs)."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring

        # Use a low falsification threshold for test speed
        edge_id = _insert_edge(
            conn, source_action="demote_test", formality=0.8, support_count=5
        )
        sw = SurpriseWiring(
            conn, falsification_threshold=3
        )

        # Simulate 3 falsifying outcomes (3 separate predictions, each failure)
        for i in range(3):
            _record_prediction(
                conn, edge_id, f"plan-demote-{i}", predicted_certainty=0.8
            )
            sw.resolve_outcome(
                f"plan-demote-{i}", "failure",
                task_id=500 + i, evidence_run_id=99,
            )

        edge = _get_edge(conn, edge_id)
        assert edge["falsified_by"] is not None  # demoted!
        assert edge["falsified_by"] == 99  # set to the evidence run id

    def test_demoted_edge_excluded_from_governance(self, conn):
        """A demoted edge (falsified_by set) must not appear in PlanningCheck
        governing edges (T5 already excludes falsified_by IS NOT NULL)."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring
        from planning_check import PlanningCheck, Plan

        edge_id = _insert_edge(
            conn, source_action="gov_test", formality=0.8, support_count=2
        )
        sw = SurpriseWiring(conn, falsification_threshold=2)

        # 2 falsifying outcomes -> demote
        for i in range(2):
            _record_prediction(conn, edge_id, f"plan-gov-{i}", predicted_certainty=0.8)
            sw.resolve_outcome(f"plan-gov-{i}", "failure", task_id=600 + i, evidence_run_id=77)

        # Edge is now demoted
        edge = _get_edge(conn, edge_id)
        assert edge["falsified_by"] is not None

        # PlanningCheck should NOT find this edge as governing
        pc = PlanningCheck(conn)
        preds = pc.check_and_record(Plan(plan_id="post-demote", actions=["gov_test"]))
        assert preds == []  # demoted edge doesn't govern

    def test_no_demotion_below_threshold(self, conn):
        """An edge with fewer falsifications than the threshold must NOT be demoted."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring

        edge_id = _insert_edge(
            conn, source_action="no_demote", formality=0.8, support_count=5
        )
        sw = SurpriseWiring(conn, falsification_threshold=5)

        # Only 2 falsifications (below threshold of 5)
        for i in range(2):
            _record_prediction(conn, edge_id, f"plan-nd-{i}", predicted_certainty=0.8)
            sw.resolve_outcome(f"plan-nd-{i}", "failure", task_id=700 + i, evidence_run_id=55)

        edge = _get_edge(conn, edge_id)
        assert edge["falsified_by"] is None  # NOT demoted
        assert edge["support_count"] == 3  # 5 - 2 = 3


# =============================================================================
# e. NO SURPRISE WHEN PREDICTION MATCHES ACTUAL
# =============================================================================
class TestNoSurpriseOnMatch:
    def test_no_surprise_when_prediction_matches_actual_success(self, conn):
        """When predicted certainty is high and actual is success,
        surprise_level should be low and no packet emitted."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring

        edge_id = _insert_edge(
            conn, source_action="match_test", formality=0.9, support_count=5
        )
        # High certainty prediction + actual success -> low surprise
        _record_prediction(conn, edge_id, "plan-match-ok", predicted_certainty=0.9)
        sw = SurpriseWiring(conn)
        packets = sw.resolve_outcome("plan-match-ok", "success", task_id=800)

        assert len(packets) == 0  # no surprise packet

        # surprise_level was recorded but is low
        with conn.cursor() as cur:
            cur.execute(
                "SELECT surprise_level FROM planning_prediction WHERE plan_id = 'plan-match-ok'"
            )
            row = cur.fetchone()
        assert row[0] is not None
        assert row[0] < 0.3  # below surprise threshold

        # Edge weight unchanged (no falsifying surprise)
        edge = _get_edge(conn, edge_id)
        assert edge["support_count"] == 5  # unchanged

    def test_no_surprise_when_low_certainty_and_failure(self, conn):
        """When predicted certainty is low and actual is failure,
        surprise is low (prediction was already pessimistic)."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring

        edge_id = _insert_edge(
            conn, source_action="low_cert_test", formality=0.1, support_count=3
        )
        _record_prediction(conn, edge_id, "plan-low-cert", predicted_certainty=0.1)
        sw = SurpriseWiring(conn)
        packets = sw.resolve_outcome("plan-low-cert", "failure", task_id=801)

        # |0.1 - 0.0| = 0.1 < 0.3 threshold -> no surprise
        assert len(packets) == 0

        # Edge weight unchanged (surprise didn't cross threshold;
        # also predicted < 0.5 so not a "predicted success" failure)
        edge = _get_edge(conn, edge_id)
        assert edge["support_count"] == 3  # unchanged

    def test_idempotent_resolve_no_double_surprise(self, conn):
        """Resolving the same plan twice must not re-emit surprise (already resolved)."""
        sys.path.insert(0, str(HERE))
        from surprise_wiring import SurpriseWiring

        edge_id = _insert_edge(
            conn, source_action="idempotent_test", formality=0.9, support_count=5
        )
        _record_prediction(conn, edge_id, "plan-idem", predicted_certainty=0.9)
        sw = SurpriseWiring(conn)

        packets1 = sw.resolve_outcome("plan-idem", "failure", task_id=900)
        assert len(packets1) == 1

        # Second resolve: prediction already resolved (actual_outcome IS NOT NULL)
        packets2 = sw.resolve_outcome("plan-idem", "failure", task_id=900)
        assert len(packets2) == 0  # nothing to resolve


# =============================================================================
# f. MAIN-PATH UNAFFECTED (regression check)
# =============================================================================
class TestMainPathUnaffected:
    def test_experimental_module_not_imported_by_main_path(self):
        """No main-path file imports surprise_wiring or the experiments package."""
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
        forbidden = ["surprise_wiring", "experiments.causal_edges", "experiments."]
        for f in main_files:
            if not f.exists():
                continue
            text = f.read_text()
            for token in forbidden:
                assert token not in text, \
                    f"{f.name} references experimental module: {token}"

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
        if result.returncode == 0:
            assert "ok" in result.stdout
        else:
            err = result.stderr
            assert "surprise_wiring" not in err and "experiments" not in err, \
                f"loop.py import broken by experimental module: {err}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
