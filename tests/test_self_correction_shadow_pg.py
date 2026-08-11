"""Real-schema integration test for the stage-1 self-correction shadow reads (#4834).

The unit tests hand-feed correction+principle rows sharing one rule string — they mirror the
mental model and CANNOT catch the live v1(mined)/v2(overturn) rule_version mismatch. This test
exercises the two ACTUAL SQL reads against a seeded governance DB end-to-end, so a real
promote(v2)+mined(v1) lineage with a real sibling MUST produce a non-empty snapshot and a
sibling_audit recommendation — NOT no_snapshot. It also pins the pre-fix behavior (the exact
rule_version filter returns zero) so the next SQL/schema drift can't silently reintroduce the bug.

Gate on the DSN like the other DB tests:
    AQ_SELFCORR_TEST_DSN='host=127.0.0.1 port=55483 dbname=aq user=aq_owner password=...' \
        python -m pytest tests/test_self_correction_shadow_pg.py -q
"""
from __future__ import annotations

import os

import pytest

from runner.consultants import self_correction_shadow as shadow
from runner.consultants.self_correction import consult, normalize_rule_identity, sibling_hypotheses
from runner.consultants.seam import ConsultantAction, Recommendation

DSN = os.environ.get("AQ_SELFCORR_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set AQ_SELFCORR_TEST_DSN to a seeded governance DB to run the real-schema test"
)


@pytest.fixture()
def db():
    from runner.db import Db
    conn = Db(DSN, graph="none", connect_retries=2)
    yield conn


def test_recent_correction_returns_the_mined_generating_rule_not_the_overturn_rule(db):
    """C1: the correction's generating rule is the MINED lineage's rule_version, not the overturn
    transition's own rule_version (which differs — e.g. mined v1 vs demote/promote v2)."""
    corr = db.self_correction_recent_correction("3650 days")
    assert corr is not None, "seeded DB has a disposition overturn but none was detected"
    correction_rule = corr["rule_version"]

    # The overturn transition's OWN rule_version for the same lineage (the value the buggy code
    # used). It differs from the mined rule_version — that difference is exactly the trap.
    overturn_rule = db._q(
        "SELECT t.rule_version FROM causal_principle_transition t "
        "JOIN causal_principle_transition m ON m.id=%s "
        "WHERE t.cause=m.cause AND t.effect=m.effect AND t.scope=m.scope "
        "  AND t.from_status IS NOT NULL AND t.to_status IS DISTINCT FROM t.from_status "
        "ORDER BY t.created_at DESC, t.id DESC LIMIT 1",
        (corr["principle_id"],), one=True,
    )["rule_version"]

    assert overturn_rule != correction_rule, (
        "expected mined and overturn rule_versions to differ (the v1/v2 trap); "
        f"mined={correction_rule} overturn={overturn_rule}"
    )
    # ...but they are the SAME normalized identity, so they are one sibling class.
    assert normalize_rule_identity(overturn_rule) == normalize_rule_identity(correction_rule)


def test_prefix_bug_exact_overturn_rule_filter_returns_zero_principles(db):
    """Pins the PRE-FIX behavior: filtering mined lineages by the overturn transition's EXACT
    rule_version (what the buggy self_correction_rule_principles did) yields ZERO rows, which made
    build_snapshot return None -> observe recorded no_snapshot on every cycle."""
    corr = db.self_correction_recent_correction("3650 days")
    assert corr is not None
    overturn_rule = db._q(
        "SELECT t.rule_version FROM causal_principle_transition t "
        "JOIN causal_principle_transition m ON m.id=%s "
        "WHERE t.cause=m.cause AND t.effect=m.effect AND t.scope=m.scope "
        "  AND t.from_status IS NOT NULL AND t.to_status IS DISTINCT FROM t.from_status "
        "ORDER BY t.created_at DESC, t.id DESC LIMIT 1",
        (corr["principle_id"],), one=True,
    )["rule_version"]

    exact = db._q(
        "WITH mined AS (SELECT DISTINCT ON (cause,effect,scope) id, rule_version "
        "  FROM causal_principle_transition WHERE transition_kind='mined' "
        "  ORDER BY cause,effect,scope, id) "
        "SELECT count(*) AS n FROM mined WHERE rule_version=%s",
        (overturn_rule,), one=True,
    )["n"]
    assert exact == 0, "pre-fix exact-string filter unexpectedly matched — bug reproduction invalid"

    # The FIXED read (normalized match) finds the real siblings that the exact filter missed.
    principles = db.self_correction_rule_principles(corr["rule_version"])
    assert len(principles) >= 2, "fixed normalized read must recover the siblings the bug hid"


def test_real_lineage_produces_nonempty_snapshot_and_sibling_audit(db):
    """The end-to-end correctness the bug broke: real lineage -> non-empty snapshot -> sibling_audit
    (NOT no_snapshot)."""
    snapshot = shadow.build_snapshot(db, window="3650 days")
    assert snapshot is not None, "a real correction + siblings must NOT yield an empty snapshot"
    assert len(snapshot.principles) >= 2

    result = consult(snapshot)
    assert isinstance(result, Recommendation), f"expected a recommendation, got {type(result).__name__}"
    assert result.action == ConsultantAction.SIBLING_AUDIT
    reopened = [s.principle_id for s in sibling_hypotheses(snapshot)]
    assert reopened, "the sibling audit must name the reopened siblings"
    assert snapshot.correction.item_id not in reopened  # the corrected item is not its own sibling


def test_observe_records_recommendation_content_end_to_end(db):
    """observe() over the real reads records a sibling_audit recommendation (content, not mere
    existence). Exercises the real telemetry write path too (requires schema/026 applied + grant)."""
    record = shadow.observe(db, work_context="pg-integration-test", window="3650 days")
    assert record is not None
    assert record["result_kind"] == "recommendation"
    assert record["action"] == "sibling_audit"
    assert record["reopened_principle_ids"]
    assert record["detail"].get("correction_kind") in {"demotion", "promotion"}
