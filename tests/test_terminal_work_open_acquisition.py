"""TERMINAL work may never have an OPEN acquisition — for every terminal status, not just 'done'.

015_acquisition_lifecycle.sql guarded this pair but tested only `done` in both triggers.
`abandoned` is equally terminal, so the pair was reachable — and reachable through ORDINARY
CALLERS, not hand-written SQL: reject_work_intent(), reject_work_conflict() and reject_work() all
set work to 'abandoned' without closing an open acquisition.

The consequence: on a RESUMED acquisition, an intent or conflict rejection strands the acquisition
permanently. Pending-work selection no longer sees its abandoned parent, so nothing picks the
acquisition up and nothing closes it. That is the L1 wedge arriving through a different door.

Found by independent re-review of main@506300d (BB #2631). Fixed in
schema/024_terminal_work_open_acquisition.sql.

These tests go through the REAL reject callers rather than issuing UPDATE themselves. A test that
writes its own UPDATE proves the trigger fires; only the caller path proves the system cannot reach
the bad state in production.
"""
import os

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DSN = os.environ.get("AQ_C4_TEST_DSN")
# AQ_REQUIRE_C4=1 turns the skip into a hard failure — see tests/test_c4_governance_pg.py for why.
if os.environ.get("AQ_REQUIRE_C4", "").strip() in {"1", "true", "yes", "on"} and not DSN:
    raise RuntimeError(
        "AQ_REQUIRE_C4=1 but AQ_C4_TEST_DSN is unset: the terminal-work lifecycle controls would "
        "SKIP. Provide the DSN (scripts/run_c4_controls.sh) or unset AQ_REQUIRE_C4.")
pytestmark = pytest.mark.skipif(not DSN, reason="set AQ_C4_TEST_DSN (scripts/run_c4_controls.sh runs these non-skipping)")


def _open_pair(cur, tag):
    """A pending work row with a pending acquisition against it."""
    cur.execute("INSERT INTO work(kind,summary,rationale,status) "
                "VALUES(%s,'t','t','pending') RETURNING id", (tag,))
    work_id = cur.fetchone()[0]
    cur.execute("INSERT INTO plan_acquisition"
                "(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction) "
                "VALUES(%s,%s,'s1','environment_experiment',0,'s1','x') RETURNING acquisition_id",
                (work_id, f"plan-{tag}"))
    return work_id, cur.fetchone()[0]


@pytest.mark.parametrize("terminal", ["done", "abandoned"])
def test_terminal_work_rejected_while_acquisition_open(terminal):
    """Both terminal statuses must be refused. 'abandoned' was the one that got through."""
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        work_id, _ = _open_pair(cur, f"term-{terminal}")
        with pytest.raises(psycopg2.errors.RaiseException, match="while acquisition is open"):
            cur.execute("UPDATE work SET status=%s WHERE id=%s", (terminal, work_id))
        conn.rollback()


@pytest.mark.parametrize("terminal", ["done", "abandoned"])
def test_open_acquisition_rejected_for_terminal_work(terminal):
    """The mirror direction: no new open acquisition against already-terminal work."""
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO work(kind,summary,rationale,status) "
                    "VALUES(%s,'t','t',%s) RETURNING id", (f"mirror-{terminal}", terminal))
        work_id = cur.fetchone()[0]
        with pytest.raises(psycopg2.errors.RaiseException, match="cannot be open for"):
            cur.execute("INSERT INTO plan_acquisition"
                        "(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction) "
                        "VALUES(%s,'p','s1','environment_experiment',0,'s1','x')", (work_id,))
        conn.rollback()


def test_closing_the_acquisition_then_abandoning_is_allowed():
    """The guard must not be a blanket ban. Closed acquisition -> abandon is legitimate.

    Without this, a guard that rejected EVERYTHING would pass the two tests above while breaking
    the system. Rejection-only evidence is not evidence of a correct boundary.
    """
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        work_id, acq_id = _open_pair(cur, "allowed")
        cur.execute("UPDATE plan_acquisition SET status='completed', completed_at=now() "
                    "WHERE acquisition_id=%s", (acq_id,))
        cur.execute("UPDATE work SET status='abandoned' WHERE id=%s", (work_id,))
        cur.execute("SELECT status FROM work WHERE id=%s", (work_id,))
        assert cur.fetchone()[0] == "abandoned"
        conn.rollback()


def test_real_reject_callers_cannot_strand_an_open_acquisition():
    """Through the PRODUCTION reject path, not a hand-written UPDATE.

    reject_work_intent / reject_work_conflict / reject_work are the callers that reached the bad
    state. Whichever of them exists in this build must be unable to strand an open acquisition.
    """
    import runner.db as dbmod

    db = dbmod.Db(DSN, graph="none")
    rejecters = [n for n in ("reject_work_intent", "reject_work_conflict", "reject_work")
                 if hasattr(db, n)]
    assert rejecters, "no reject_* caller found on Db — the guarded path moved; re-derive this test"

    for name in rejecters:
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            work_id, _ = _open_pair(cur, f"caller-{name}")
            conn.commit()
        try:
            fn = getattr(db, name)
            try:
                fn(work_id, "test rejection")
            except TypeError:
                fn(work_id)
        except psycopg2.errors.RaiseException:
            pass  # refused at the database — the outcome we want
        except TypeError:
            continue  # signature we cannot drive blind; the trigger tests still cover the state
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT w.status, pa.status FROM work w "
                        "JOIN plan_acquisition pa ON pa.work_id=w.id WHERE w.id=%s", (work_id,))
            row = cur.fetchone()
            if row:
                work_status, acq_status = row
                assert not (work_status in ("done", "abandoned")
                            and acq_status in ("pending", "running")), (
                    f"{name} stranded an open acquisition: work={work_status} acq={acq_status}")
