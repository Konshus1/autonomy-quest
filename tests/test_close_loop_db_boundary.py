"""Imported coding work can never fall through to the mission ACT selector."""
from runner.db import Db


class CaptureDb:
    approved_work = Db.approved_work
    pending_autonomous_work = Db.pending_autonomous_work

    def __init__(self):
        self.queries = []

    def _q(self, sql, args=(), one=False):
        self.queries.append(sql)
        return None


def test_both_mission_selectors_explicitly_exclude_worker_reviewer_rows():
    db = CaptureDb()
    db.approved_work()
    db.pending_autonomous_work()
    assert len(db.queries) == 2
    assert all("w.execution_path='mission'" in query for query in db.queries)
    assert all("worker_reviewer" not in query for query in db.queries)
