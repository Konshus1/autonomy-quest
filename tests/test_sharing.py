"""Share/import pipeline — the public INERTNESS claim, test-backed (proof-track audit).

Public claim under test (runner/sharing.py docstring + README):
    "A GENERALISABLE LABEL IS A PROPOSAL, NOT PROOF. Imports are STAGED and INERT. They
    reach no decision until this instance adjudicates them."

What these tests prove, at the CONTRACT level:
  - import_bundle STAGES every learning via db.stage_import and NEVER touches the live
    learnings write path (the fake has no such method — a stray live write is a loud error).
  - the bundle format is gated; falsifier-less claims are staged but MARKED honestly;
    re-importing the same bundle is idempotent (already_had accounting).
  - export only draws from db.exportable_learnings (the generalisable+evidence filter) and
    ships the arguable-claim shape (evidence + applies_when + falsified_by slots).
  - the REAL Db.promote_import / reject_import / rollback_import gate logic (run against a
    stubbed _q choke point — the real code, not a re-implementation):
      * promotion without LOCAL adjudication evidence is REFUSED before any live write;
      * applies_here must be True; all four evidence fields are required;
      * a high-confidence claim may not be adjudicated by its own importer (independence);
      * a valid promotion is the ONLY path that writes the live learnings table;
      * reject marks rejected; rollback deletes the local learning and marks rolled_back.
  - the inertness separation at the SQL the code actually emits: live_learnings() reads
    ONLY `learnings`; stage_import() writes ONLY `shared_learnings`.

Scope honesty: StubQDb stubs Db._q (the single SQL choke point) — these tests prove the
code PATHS and the SQL TEXT the code emits, not a live Postgres's table semantics
(the schema-level CHECK constraint that makes a promotion without local evidence
unrepresentable is verified only by a real database; no test-database harness exists in
this repo today — flagged as a follow-up, not claimed here).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from runner import sharing
from runner.db import Db, PromotionRefused


# --------------------------------------------------------------------------------------
# sharing.py (module-level export/import) against a recording fake Db
# --------------------------------------------------------------------------------------

class FakeSharingDb:
    """Records every call. Deliberately has NO live-learnings write method (no
    write_learning / no learnings-table access): if sharing.py ever tried to adopt an
    import directly into the live path, the AttributeError fails the test loudly."""

    def __init__(self, export_rows=(), dedup_on_second=False):
        self.calls = []
        self._export_rows = list(export_rows)
        self._dedup_on_second = dedup_on_second
        self._stage_count = 0

    def exportable_learnings(self):
        self.calls.append(("exportable_learnings",))
        return list(self._export_rows)

    def stage_import(self, **kw):
        self.calls.append(("stage_import", kw))
        self._stage_count += 1
        if self._dedup_on_second and self._stage_count > 1:
            return None  # ON CONFLICT DO NOTHING -> already had it
        return self._stage_count


def _bundle(path, learnings, fmt="autonomy-quest/learning-bundle/1"):
    payload = {
        "format": fmt,
        "origin_instance": "origin-box",
        "origin_mission": "their mission",
        "learnings": learnings,
    }
    path.write_text(json.dumps(payload))
    return str(path)


def test_import_stages_every_learning_and_never_touches_the_live_path(tmp_path):
    db = FakeSharingDb()
    p = _bundle(tmp_path / "b.json", [
        {"insight": "a", "confidence": 0.6, "origin_run_id": 1, "origin_evidence": "ev-a",
         "applies_when": "when x", "falsified_by": "if y"},
        {"insight": "b", "confidence": 0.7, "origin_run_id": 2, "origin_evidence": "ev-b",
         "applies_when": "when z", "falsified_by": "if w"},
    ])
    r = sharing.import_bundle(db, p)
    assert r["staged"] == 2 and r["already_had"] == 0
    # EVERY db interaction was a stage_import — nothing else was called, and the fake's
    # lack of a live-write method means any adoption attempt would have errored above.
    assert all(c[0] == "stage_import" for c in db.calls), db.calls
    assert "INERT" in r["status"]


def test_import_rejects_unknown_bundle_format(tmp_path):
    db = FakeSharingDb()
    p = _bundle(tmp_path / "bad.json", [{"insight": "a"}], fmt="totally/different/9")
    with pytest.raises(ValueError):
        sharing.import_bundle(db, p)
    assert db.calls == [], "a rejected bundle must stage nothing"


def test_import_marks_falsifier_less_claims_honestly(tmp_path):
    db = FakeSharingDb()
    p = _bundle(tmp_path / "b.json", [
        {"insight": "no falsifier given", "confidence": 0.5, "origin_evidence": "ev"},
    ])
    r = sharing.import_bundle(db, p)
    assert r["staged"] == 1  # still staged — but marked, not papered over
    kw = db.calls[0][1]
    assert "NO FALSIFIER GIVEN" in kw["falsified_by"]
    assert "did not say" in kw["applies_when"]


def test_import_is_idempotent_on_reimport(tmp_path):
    db = FakeSharingDb(dedup_on_second=True)
    p = _bundle(tmp_path / "b.json", [
        {"insight": "same claim", "confidence": 0.6, "origin_evidence": "ev",
         "falsified_by": "if wrong"},
    ])
    sharing.import_bundle(db, p)
    r2 = sharing.import_bundle(db, p)  # the same bundle a second time
    assert r2["staged"] == 0 and r2["already_had"] == 1


def test_export_draws_only_from_exportable_learnings_and_ships_arguable_shape(tmp_path):
    rows = [
        {"insight": "transfers", "confidence": 0.9, "run_id": 7, "evidence": "proof-ish",
         "origin_outcome": "succeeded"},
    ]
    db = FakeSharingDb(export_rows=rows)
    inst = SimpleNamespace(mission=SimpleNamespace(objective="our mission"))
    out = tmp_path / "out.json"
    b = sharing.export(db, inst, str(out))
    assert db.calls == [("exportable_learnings",)], db.calls
    assert b["format"] == "autonomy-quest/learning-bundle/1"
    assert "PROPOSAL" in b["note"]
    item = b["learnings"][0]
    # the arguable-claim shape: evidence attached + slots for when it applies / what disproves it
    assert item["origin_evidence"] == "proof-ish"
    assert "applies_when" in item and "falsified_by" in item
    on_disk = json.loads(out.read_text())
    assert on_disk["learnings"][0]["insight"] == "transfers"


# --------------------------------------------------------------------------------------
# The REAL Db gate logic (promote / reject / rollback) against a stubbed _q choke point
# --------------------------------------------------------------------------------------

class StubQDb(Db):
    """Runs the REAL promote_import / reject_import / rollback_import / live_learnings /
    stage_import code with a capturing _q. No connection is opened."""

    def __init__(self, staged_row=None):
        self._staged_row = staged_row
        self.queries = []  # (sql, args)

    def _q(self, sql, args=(), one=False):  # type: ignore[override]  # test stub: canned rows, not RealDictRows
        self.queries.append((sql, args))
        if "FROM shared_learnings WHERE id=%s AND status='staged'" in sql:
            return self._staged_row
        if "SELECT local_learning_id FROM shared_learnings" in sql:
            return {"local_learning_id": 42}
        if sql.lstrip().startswith("INSERT INTO learnings"):
            return {"id": 99}
        if "INSERT INTO shared_learnings" in sql:
            return {"id": 5}
        return None

    def sql_text(self) -> str:
        return "\n".join(sql for sql, _ in self.queries)


def _staged(confidence=0.6, imported_by="alice"):
    return {
        "id": 5, "insight": "foreign claim", "confidence": confidence,
        "origin_instance": "origin-box", "origin_mission": "their mission",
        "origin_evidence": "their evidence", "imported_by": imported_by,
        "status": "staged",
    }


def _full_evidence():
    return {
        "applies_here": True,
        "applies_here_how": "ran the predicate against our mission",
        "negative_control": "pytest tests/test_x.py",
        "negative_control_result": "would have failed if the claim were false",
        "evidence_ref": "run #123",
    }


def test_promotion_without_local_evidence_is_refused_before_any_live_write():
    db = StubQDb(staged_row=_staged())
    with pytest.raises(PromotionRefused):
        db.promote_import(5, by="bob", why="sounds good", evidence={})
    assert "INSERT INTO learnings" not in db.sql_text(), \
        "a refused promotion must perform ZERO live writes"


def test_promotion_requires_applies_here_true():
    db = StubQDb(staged_row=_staged())
    e = _full_evidence()
    e["applies_here"] = False
    with pytest.raises(PromotionRefused):
        db.promote_import(5, by="bob", why="why", evidence=e)
    assert "INSERT INTO learnings" not in db.sql_text()


def test_promotion_requires_all_four_evidence_fields():
    db = StubQDb(staged_row=_staged())
    e = _full_evidence()
    e["negative_control"] = ""   # ran nothing that could have disproved it
    with pytest.raises(PromotionRefused):
        db.promote_import(5, by="bob", why="why", evidence=e)
    assert "INSERT INTO learnings" not in db.sql_text()


def test_high_confidence_claim_may_not_be_adjudicated_by_its_own_importer():
    db = StubQDb(staged_row=_staged(confidence=0.9, imported_by="alice"))
    with pytest.raises(PromotionRefused):
        db.promote_import(5, by="alice", why="I reviewed my own import", evidence=_full_evidence())
    assert "INSERT INTO learnings" not in db.sql_text(), \
        "importer blessing their own high-confidence import must write nothing"


def test_independent_adjudicator_may_promote_high_confidence_claim():
    db = StubQDb(staged_row=_staged(confidence=0.9, imported_by="alice"))
    lid = db.promote_import(5, by="bob", why="bob independently verified", evidence=_full_evidence())
    assert lid == 99
    sql = db.sql_text()
    assert "INSERT INTO learnings" in sql, "the valid path is the ONLY live write"
    assert "status='promoted'" in sql


def test_valid_promotion_records_the_local_negative_control_in_the_learning():
    db = StubQDb(staged_row=_staged())
    lid = db.promote_import(5, by="bob", why="verified locally", evidence=_full_evidence())
    assert lid == 99
    insert_args = next(args for sql, args in db.queries if sql.lstrip().startswith("INSERT INTO learnings"))
    evidence_text = insert_args[1]
    assert "OUR negative control" in evidence_text and "run #123" in evidence_text, \
        "the adopted learning must carry THIS instance's adjudication trail, not just the foreign claim"


def test_reject_marks_rejected_and_writes_nothing_live():
    db = StubQDb(staged_row=_staged())
    db.reject_import(5, by="bob", why="does not hold here")
    sql = db.sql_text()
    assert "status='rejected'" in sql
    assert "INSERT INTO learnings" not in sql


def test_rollback_deletes_the_local_learning_and_marks_rolled_back():
    db = StubQDb(staged_row=_staged())
    db.rollback_import(5, by="bob", why="turned out wrong here")
    sql = db.sql_text()
    assert "DELETE FROM learnings" in sql
    assert "status='rolled_back'" in sql


# --------------------------------------------------------------------------------------
# The inertness separation in the SQL the code actually emits
# --------------------------------------------------------------------------------------

def test_live_learnings_sql_never_reads_the_shared_staging_table():
    db = StubQDb()
    db.live_learnings()
    live_sql = db.queries[-1][0]
    assert "FROM learnings" in live_sql
    assert "shared_learnings" not in live_sql, \
        "the loop's belief read must never see staged imports — that is the inertness claim"


def test_stage_import_sql_writes_only_the_staging_table():
    db = StubQDb()
    db.stage_import(origin_instance="o", origin_mission="m", origin_run_id=1,
                    origin_evidence="e", origin_outcome="s", insight="i", confidence=0.5,
                    applies_when="w", falsified_by="f", imported_by="alice")
    stage_sql = db.queries[-1][0]
    assert "INSERT INTO shared_learnings" in stage_sql
    assert "INSERT INTO learnings" not in stage_sql, \
        "staging an import must never write the live learnings table"
