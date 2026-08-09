"""test_toy_demo.py — EXPERIMENTAL tests for T7 (#4899).

Five things must be true:
  a. Demo runs end-to-end without error.
  b. Edge lifecycle visible in graph dump (created -> predicted -> outcome ->
     surprise -> weight update).
  c. Surprise fires when prediction is wrong (predicted success, actual failure).
  d. No surprise when prediction is right (predicted success, actual success).
  e. Frame-expansion gap honestly recorded (absence is a finding).

Plus main-path regression: the toy demo (and T5/T6 it imports) is not imported
by any main-path file, and the migration is additive (no existing table altered).

Tests use a DEDICATED throwaway database (aq_t7_test) so the real postgres DB
is never touched. The DB is dropped+created fresh per run, and the T5
migration (009_causal_edges.sql) is applied.

Run:
    cd ~/src/autonomy-quest-public-19ea8b6
    python3 -m pytest experiments/causal_edges/test_toy_demo.py -v
  or
    python3 experiments/causal_edges/test_toy_demo.py
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
TEST_DB = os.environ.get("AQ_T7_TEST_DB", "aq_t7_test")
DB_HOST = os.environ.get("AQ_T7_TEST_HOST", "localhost")
DB_PORT = os.environ.get("AQ_T7_TEST_PORT", "5432")
DB_USER = os.environ.get("AQ_T7_TEST_USER", os.environ.get("AQ_T5_TEST_USER", "kthomas"))
ADMIN_DB = os.environ.get("AQ_T7_ADMIN_DB", "postgres")


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


# -- import helper ------------------------------------------------------------
def _import_demo():
    sys.path.insert(0, str(HERE))
    import toy_demo
    import importlib
    importlib.reload(toy_demo)
    return toy_demo


# =============================================================================
# a. DEMO RUNS END-TO-END WITHOUT ERROR
# =============================================================================
class TestEndToEnd:
    def test_demo_runs_without_error_failure_path(self, conn):
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        assert isinstance(dump, dict)
        assert dump["task"] == "T7_toy_demo"
        assert "lifecycle" in dump
        assert "graph" in dump
        assert "frame_expansion" in dump

    def test_demo_runs_without_error_success_path(self, conn):
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="success")
        assert isinstance(dump, dict)
        assert dump["mission_outcome"] == "success"

    def test_analogy_retrieval_picks_structurally_nearest(self, conn):
        """The analogy should retrieve the episode with the highest dimension
        overlap with the novel mission (E1, cold-outreach follow-up)."""
        toy = _import_demo()
        eps = toy.seed_episode_library()
        m = toy.novel_mission()
        analogy, score = toy.retrieve_analogy(eps, m)
        assert analogy.eid == "E1"  # 4-dimension overlap, highest Jaccard
        assert score > 0.0


# =============================================================================
# b. EDGE LIFECYCLE VISIBLE IN GRAPH DUMP
# (created -> predicted -> outcome -> surprise -> weight update)
# =============================================================================
class TestEdgeLifecycle:
    def test_lifecycle_phases_all_present(self, conn):
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        lc = dump["lifecycle"]
        for phase in ("created", "predicted", "acted", "surprise", "weight_update", "demoted"):
            assert phase in lc, f"lifecycle missing phase: {phase}"

    def test_edge_created_in_graph(self, conn):
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        assert dump["edge_id"] is not None
        edges = dump["graph"]["edges"]
        assert any(e["edge_id"] == dump["edge_id"] for e in edges)

    def test_prediction_recorded_in_graph(self, conn):
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        preds = dump["graph"]["predictions"]
        assert len(preds) >= 1
        # filter to THIS run's edge (module-scoped DB accumulates across tests)
        mine = [p for p in preds if p["edge_id"] == dump["edge_id"]]
        assert len(mine) >= 1
        assert mine[0]["predicted_certainty"] is not None

    def test_outcome_and_surprise_recorded_in_graph(self, conn):
        """After the run, the prediction row must have actual_outcome + surprise_level filled."""
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        preds = dump["graph"]["predictions"]
        p = next(p for p in preds if p["edge_id"] == dump["edge_id"])
        assert p["actual_outcome"] == "failure"
        assert p["surprise_level"] is not None

    def test_weight_update_visible_failure_path(self, conn):
        """On a falsifying surprise (predicted success, actual failure),
        support_count must decrease."""
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        lc = dump["lifecycle"]
        before = lc["weight_update"]["support_count_before"]
        after = lc["weight_update"]["support_count_after"]
        assert after < before, f"support_count should decrease: {before} -> {after}"
        assert lc["weight_update"]["decreased"] is True


# =============================================================================
# c. SURPRISE FIRES WHEN PREDICTION IS WRONG
# =============================================================================
class TestSurpriseFires:
    def test_surprise_packet_emitted_on_failure(self, conn):
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        assert dump["lifecycle"]["surprise"]["fired"] is True
        assert dump["lifecycle"]["surprise"]["n_packets"] >= 1
        levels = dump["lifecycle"]["surprise"]["surprise_levels"]
        assert all(lv >= 0.3 for lv in levels)  # above surprise threshold

    def test_packet_gate_held_fail_closed(self, conn):
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        for pkt in dump["surprise_packets"]:
            assert pkt["gate"] == "held_fail_closed"
            auth = pkt["authority"]
            assert auth["may_dispatch_workers"] is False
            assert auth["may_mutate_production"] is False
            assert auth["requires_manager_review"] is True
            assert pkt["spawn_recommended"] is False


# =============================================================================
# d. NO SURPRISE WHEN PREDICTION IS RIGHT
# =============================================================================
class TestNoSurpriseOnMatch:
    def test_no_surprise_on_success(self, conn):
        """Predicted success + actual success -> surprise below threshold -> no packet."""
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="success")
        assert dump["lifecycle"]["surprise"]["fired"] is False
        assert dump["lifecycle"]["surprise"]["n_packets"] == 0

    def test_no_weight_update_on_success(self, conn):
        """On a matching outcome, support_count must NOT decrease."""
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="success")
        lc = dump["lifecycle"]
        before = lc["weight_update"]["support_count_before"]
        after = lc["weight_update"]["support_count_after"]
        assert after == before, f"support_count changed on success: {before} -> {after}"
        assert lc["weight_update"]["decreased"] is False

    def test_predicted_certainty_high_enough_for_no_surprise_on_success(self, conn):
        """Sanity: the proposed edge yields a predicted certainty close enough
        to 1.0 that |certainty - 1.0| < 0.3 (no surprise on success)."""
        toy = _import_demo()
        eps = toy.seed_episode_library()
        m = toy.novel_mission()
        analogy, _ = toy.retrieve_analogy(eps, m)
        spec = toy.propose_edge_from_analogy(analogy, m)
        from planning_check import _composite_certainty
        cert = _composite_certainty(
            spec["formality_weight"], spec["strictness_weight"], spec["directness_weight"]
        )
        # |cert - 1.0| must be < 0.3 for no-surprise-on-success
        assert abs(cert - 1.0) < 0.3, f"predicted certainty {cert} too far from 1.0"
        # and |cert - 0.0| must be >= 0.3 for surprise-on-failure
        assert abs(cert - 0.0) >= 0.3, f"predicted certainty {cert} too close to 0.0"


# =============================================================================
# e. FRAME-EXPANSION GAP HONESTLY RECORDED (absence is a finding)
# =============================================================================
class TestFrameExpansion:
    def test_uncaptured_attribute_present(self, conn):
        """Episode E2 has 'reversibility' which no dimension in the library captures."""
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        fe = dump["frame_expansion"]
        attrs = fe["uncaptured_attributes"]
        assert len(attrs) >= 1
        names = {a["attribute"] for a in attrs}
        assert "reversibility" in names
        # and it's genuinely not in the dimension library
        assert "reversibility" not in toy.KNOWN_DIMENSIONS

    def test_gap_noticed_is_false(self, conn):
        """The system must NOT claim it noticed the gap — frame expansion (T11)
        is not wired. Honest negative finding."""
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        fe = dump["frame_expansion"]
        assert fe["gap_noticed"] is False
        assert fe["analogy_layer_inspected_extra_attributes"] is False

    def test_reason_recorded(self, conn):
        """A human-readable reason must explain why the gap was not noticed."""
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        fe = dump["frame_expansion"]
        assert isinstance(fe["reason"], str) and len(fe["reason"]) > 20
        assert "frame" in fe["reason"].lower() or "dimension" in fe["reason"].lower()

    def test_reversibility_attribute_is_on_episode_E2(self, conn):
        toy = _import_demo()
        dump = toy.run_toy_demo(conn, mission_outcome="failure")
        e2 = next(e for e in dump["episodes"] if e["eid"] == "E2")
        assert "reversibility" in e2["extra_attributes"]


# =============================================================================
# f. MAIN-PATH UNAFFECTED (regression check)
# =============================================================================
class TestMainPathUnaffected:
    def test_experimental_modules_not_imported_by_main_path(self):
        """No main-path file imports toy_demo, planning_check, surprise_wiring,
        or the experiments package."""
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
        forbidden = ["toy_demo", "planning_check", "surprise_wiring",
                     "experiments.causal_edges", "experiments."]
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
        """runner.loop must still import without the experimental modules."""
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '.'); import runner.loop; print('ok')"],
            cwd=str(AQ_ROOT),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            assert "ok" in result.stdout
        else:
            err = result.stderr
            for bad in ("toy_demo", "planning_check", "surprise_wiring"):
                assert bad not in err, f"loop.py import broken by experimental module: {err}"
            assert "experiments" not in err, \
                f"loop.py import broken by experimental package: {err}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
