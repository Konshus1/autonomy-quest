from runner.db import Db


class Cursor:
    def __init__(self):
        self.args = None
    def execute(self, sql, args):
        self.sql, self.args = sql, args
    def fetchone(self):
        return (7,)


def test_actor_reflection_cannot_persist_as_certain_verified_evidence():
    cur = Cursor()
    lid = Db.write_learning(object(), cur, 1, "run #1 SUCCEEDED=True", "actor said so",
                            "local", 1.0)
    assert lid == 7
    assert "evidence_kind" in cur.sql
    assert cur.args[-1] == "actor_claim"
    assert cur.args[-2] == 0.99


def test_verified_evidence_requires_explicit_typed_producer():
    cur = Cursor()
    Db.write_learning(object(), cur, 1, "checked", "receipt", "local", 1.0,
                      evidence_kind="verified_evidence")
    assert cur.args[-1] == "verified_evidence"
    assert cur.args[-2] == 1.0
