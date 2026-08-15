"""Substrate binding (design_authority_envelope.md substrate wall; BB #2716).

Binds a capability CLASS to the REAL substrate (aq_actor grants + container tool manifest) instead of
the actor's declaration. This suite proves:

  * MANIFEST is sourced from GROUND TRUTH: the real schema/999 grant set parses to the curated
    business-table write surface; the standard image tool set leaves the external classes tool-less.
  * STRUCTURAL ABSENCE is PROVEN, never assumed: a class is ABSENT only with positive evidence; an
    unknown source (missing grant file / undeclared tools) fails CLOSED to UNKNOWN, and the envelope
    GATES unknown -- never opens it.
  * RED-FIRST envelope wiring: with a manifest, a step CLAIMING a structurally-absent high-consequence
    class is surfaced as a substrate MISMATCH (red flag, held) -- a field that is empty pre-fix
    (manifest=None). A reachable class stays gated-by-default. An internal reversible aqdb write stays
    auto. ONE-WAY: an absent class is held even if the operator opened it.
  * FLAG OFF => byte-identical: AQ_SUBSTRATE_BINDING off hands the envelope no manifest, so the
    categorical gate is exactly the Slice-2 behaviour.
"""
import textwrap
import types
from decimal import Decimal

import pytest

from runner.authority_envelope import (
    CLASS_COMMIT, CLASS_CONTACT_HUMAN, CLASS_DELETE_PROD, CLASS_DEPLOY, CLASS_EXTERNAL_WRITE,
    CLASS_INTERNAL_REVERSIBLE, CLASS_PUBLISH_EXTERNAL, CLASS_SPEND_MONEY, DEFAULT_ENVELOPE,
    HIGH_CONSEQUENCE_CLASSES, assess_action_envelope, assess_plan_envelope, from_config,
)
from runner.loop import Loop
from runner.substrate_manifest import (
    ABSENT, REACHABLE, TOOLS_UNKNOWN, UNKNOWN, ActorGrants, SubstrateManifest,
    build_substrate_manifest, enabled, parse_actor_grants,
)


# ==============================================================================================
# manifest sourced from GROUND TRUTH (the real schema/999 + standard image tools)
# ==============================================================================================
def _real_manifest():
    """The manifest the loop builds by default: real schema/999 + STANDARD_IMAGE_TOOLS, no operator
    tool override in the environment."""
    return build_substrate_manifest(env={})


def test_real_grants_parse_to_curated_business_surface():
    m = _real_manifest()
    assert m.actor_grants.known is True
    assert m.actor_grants.writable_tables == frozenset({"customers", "subscriptions"})
    # aq_actor holds no TRUNCATE and no write on any authority/evidence table.
    assert m.actor_grants.has_destructive_authority is False


def test_standard_image_leaves_external_classes_structurally_absent():
    m = _real_manifest()
    assert set(m.absent_high_consequence_classes()) == {
        CLASS_SPEND_MONEY, CLASS_CONTACT_HUMAN, CLASS_PUBLISH_EXTERNAL, CLASS_DEPLOY}


def test_curl_and_git_keep_their_classes_reachable():
    m = _real_manifest()
    assert m.is_reachable(CLASS_EXTERNAL_WRITE) is True   # curl is installed
    assert m.is_reachable(CLASS_COMMIT) is True           # git is installed
    assert m.is_reachable(CLASS_DELETE_PROD) is True       # actor holds a row-DELETE grant
    assert m.is_reachable(CLASS_INTERNAL_REVERSIBLE) is True


# ==============================================================================================
# absence is PROVEN, never assumed -- fail-closed on unknown sources
# ==============================================================================================
def test_all_unknown_manifest_fails_closed_never_absent():
    """Undeclared tools AND missing grant file => every class UNKNOWN, none ABSENT, none open."""
    m = build_substrate_manifest(grants_sql_path="/no/such/file.sql",
                                 installed_tools=TOOLS_UNKNOWN, env={})
    assert m.tools_known is False and m.actor_grants.known is False
    for c in HIGH_CONSEQUENCE_CLASSES:
        assert m.status(c) == UNKNOWN
        assert m.is_absent(c) is False        # UNKNOWN is never ABSENT (fail-closed)
    assert m.absent_high_consequence_classes() == ()


def test_missing_grants_leaves_db_class_unknown_but_tool_classes_still_absent():
    """Tools known, grants missing: a tool-only class can still be PROVEN absent; a class that reaches
    through the DB (delete_prod) degrades to UNKNOWN rather than being assumed absent."""
    m = build_substrate_manifest(grants_sql_path="/no/such/file.sql", env={})
    assert m.is_absent(CLASS_SPEND_MONEY) is True         # tool source known, no payment tool
    assert m.status(CLASS_DELETE_PROD) == UNKNOWN         # needs the grant set -> fail-closed
    assert m.is_absent(CLASS_DELETE_PROD) is False


def test_operator_tools_env_can_make_a_class_reachable(monkeypatch):
    m = build_substrate_manifest(env={"AQ_SUBSTRATE_TOOLS": "stripe, sendmail"})
    assert m.is_reachable(CLASS_SPEND_MONEY) is True       # stripe present
    assert m.is_reachable(CLASS_CONTACT_HUMAN) is True     # sendmail present
    assert m.is_absent(CLASS_DEPLOY) is True               # still no deploy tool


def test_empty_tools_env_is_known_empty_not_unknown():
    """An explicit empty AQ_SUBSTRATE_TOOLS is a KNOWN 'nothing installed', distinct from unknown."""
    m = build_substrate_manifest(env={"AQ_SUBSTRATE_TOOLS": ""})
    assert m.tools_known is True
    assert m.is_absent(CLASS_EXTERNAL_WRITE) is True       # even curl now declared absent


def test_installed_tools_argument_wins_over_env():
    m = build_substrate_manifest(installed_tools=["git"], env={"AQ_SUBSTRATE_TOOLS": "stripe"})
    assert m.is_reachable(CLASS_COMMIT) is True
    assert m.is_absent(CLASS_SPEND_MONEY) is True          # env ignored when arg supplied


def test_tool_alias_binds(monkeypatch):
    # "python" alias resolves to python3; here prove "http" alias binds external_write via httpie.
    m = build_substrate_manifest(installed_tools=["http"], env={})
    assert m.is_reachable(CLASS_EXTERNAL_WRITE) is True


# ==============================================================================================
# grant parser
# ==============================================================================================
def test_parse_actor_grants_grant_then_revoke_order():
    sql = """
      EXECUTE 'GRANT INSERT, UPDATE, DELETE ON customers, subscriptions TO aq_actor';
      EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON runs, work FROM aq_actor';
      EXECUTE 'GRANT SELECT ON customers TO aq_actor';
    """
    g = parse_actor_grants(sql)
    assert g.known is True
    assert g.writable_tables == frozenset({"customers", "subscriptions"})
    assert g.has_delete_any is True
    assert g.has_destructive_authority is False


def test_parse_actor_grants_authority_write_is_destructive():
    sql = "EXECUTE 'GRANT INSERT, UPDATE, DELETE ON runs TO aq_actor';"
    g = parse_actor_grants(sql)
    assert g.has_destructive_authority is True             # write on an authority table


def test_parse_actor_grants_absent_role_is_unknown():
    g = parse_actor_grants("GRANT SELECT ON customers TO aq_loop;")
    assert g.known is False                                # no aq_actor statement at all


def test_parse_ignores_sequence_and_select_only():
    sql = """
      EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE subscriptions_id_seq TO aq_actor';
      EXECUTE 'GRANT SELECT ON customers, subscriptions TO aq_actor';
    """
    g = parse_actor_grants(sql)
    assert g.known is True and g.writable_tables == frozenset()   # no WRITE privilege anywhere


# ==============================================================================================
# envelope substrate binding -- RED-FIRST (the field is empty pre-fix / manifest=None)
# ==============================================================================================
def test_absent_claimed_class_is_substrate_mismatch_red_flag():
    """RED->GREEN: a step CLAIMING spend_money while the substrate lacks a payment tool is held as a
    substrate MISMATCH. Pre-fix (manifest=None) the mismatch field is empty."""
    m = _real_manifest()
    v = assess_action_envelope("do a thing", DEFAULT_ENVELOPE, spends_money=True, manifest=m)
    assert v.gated is True
    assert v.substrate_mismatch_classes == (CLASS_SPEND_MONEY,)
    assert CLASS_SPEND_MONEY not in v.gated_classes       # pulled OUT of the plain gate
    assert v.substrate_bound is True

    pre_fix = assess_action_envelope("do a thing", DEFAULT_ENVELOPE, spends_money=True, manifest=None)
    assert pre_fix.substrate_mismatch_classes == ()       # the pre-fix behaviour it must beat
    assert CLASS_SPEND_MONEY in pre_fix.gated_classes


def test_keyword_derived_absent_class_is_mismatch():
    m = _real_manifest()
    v = assess_action_envelope("publish to twitter the launch", DEFAULT_ENVELOPE, manifest=m)
    assert v.gated is True and v.substrate_mismatch_classes == (CLASS_PUBLISH_EXTERNAL,)


def test_reachable_high_consequence_class_stays_normally_gated():
    m = _real_manifest()
    v = assess_action_envelope("fire an http post to the external api", DEFAULT_ENVELOPE, manifest=m)
    assert v.gated is True
    assert CLASS_EXTERNAL_WRITE in v.gated_classes         # reachable -> ordinary gate
    assert v.substrate_mismatch_classes == ()             # NOT a mismatch


def test_internal_reversible_aqdb_write_stays_auto_with_manifest():
    m = _real_manifest()
    v = assess_action_envelope("upsert the customer subscription record in aqdb",
                               DEFAULT_ENVELOPE, manifest=m)
    assert v.gated is False and v.substrate_mismatch_classes == ()


def test_unknown_class_fails_closed_to_gate_not_mismatch_not_open():
    """A high-consequence class whose substrate is UNKNOWN is GATED (default) -- never opened, and not
    a mismatch (absence was NOT proven)."""
    m = build_substrate_manifest(grants_sql_path="/no/such/file.sql", installed_tools=TOOLS_UNKNOWN, env={})
    v = assess_action_envelope("do a thing", DEFAULT_ENVELOPE, spends_money=True, manifest=m)
    assert v.gated is True
    assert CLASS_SPEND_MONEY in v.gated_classes           # unknown -> treated reachable -> gated
    assert v.substrate_mismatch_classes == ()             # NOT absent -> NOT a mismatch


def test_one_way_absent_class_held_even_when_operator_opened_it():
    """ONE-WAY: opening a class does NOT let an absent-but-claimed step through -- the plan-vs-substrate
    mismatch is surfaced regardless of disposition. The manifest only ever raises."""
    m = _real_manifest()
    env = from_config({c: "open" for c in HIGH_CONSEQUENCE_CLASSES})
    v = assess_action_envelope("do a thing", env, spends_money=True, manifest=m)
    assert v.gated is True and v.substrate_mismatch_classes == (CLASS_SPEND_MONEY,)


def test_must_ask_first_still_gates_with_manifest():
    m = _real_manifest()
    env = from_config({c: "open" for c in HIGH_CONSEQUENCE_CLASSES})
    v = assess_action_envelope("export the report", env, must_ask_first=["export"], manifest=m)
    assert v.gated is True and v.must_ask_first_hit is True


def test_verdict_authority_policy_includes_substrate_fields():
    m = _real_manifest()
    ap = assess_action_envelope("do a thing", DEFAULT_ENVELOPE, spends_money=True,
                                manifest=m).to_authority_policy()
    assert ap["substrate_bound"] is True
    assert CLASS_SPEND_MONEY in ap["substrate_mismatch_classes"]


def test_plan_merges_substrate_mismatch_across_steps():
    m = _real_manifest()
    plan = {"steps": [{"action": "query aqdb"}, {"action": "send email to the customer"}]}
    v = assess_plan_envelope(plan, DEFAULT_ENVELOPE, manifest=m)
    assert v.gated is True and CLASS_CONTACT_HUMAN in v.substrate_mismatch_classes


def test_plan_without_manifest_is_byte_identical_partition():
    """No manifest => the absent class stays in the plain gate, mismatch empty (Slice-2 behaviour)."""
    plan = {"steps": [{"action": "send email to the customer"}]}
    v = assess_plan_envelope(plan, DEFAULT_ENVELOPE, manifest=None)
    assert v.substrate_mismatch_classes == () and CLASS_CONTACT_HUMAN in v.gated_classes
    assert v.substrate_bound is False


# ==============================================================================================
# LOOP wiring: human_gate_reasons -- flag-gated, byte-identical OFF
# ==============================================================================================
def _inst(envelope=DEFAULT_ENVELOPE, must_ask=(), gate_level=3, level="act-external", approval=3.0):
    autonomy = types.SimpleNamespace(level=level, blast_radius_gate_level=gate_level,
                                     per_plan_approval_usd=approval)
    return types.SimpleNamespace(
        budget=types.SimpleNamespace(autonomy=autonomy),
        mission=types.SimpleNamespace(boundaries=types.SimpleNamespace(must_ask_first=list(must_ask))),
        authority_envelope=envelope)


def _work(action, blast_level=0, spends_money=False, touches_human=False, commits=False,
          expense="0", reversible=True, requires_human=False):
    facts = {"affected_entities_upper_bound": 1, "public_or_unbounded": False,
             "production_wide": False, "irreversible_external_write": False}
    return types.SimpleNamespace(
        requires_human=requires_human, reversible=reversible, expected_expense_usd=Decimal(expense),
        blast_radius_level=blast_level, spends_money=spends_money, touches_human=touches_human,
        commits=commits,
        plan={"steps": [{"step_id": "s1", "action": action, "blast_radius": facts}]})


def _reasons(work, inst):
    return Loop.human_gate_reasons(types.SimpleNamespace(inst=inst), work)


def _has_substrate_mismatch(reasons):
    return any("substrate mismatch" in r.lower() for r in reasons)


@pytest.fixture(autouse=True)
def _clear_tools_env(monkeypatch):
    # The loop builds the manifest from the real schema/999 + STANDARD_IMAGE_TOOLS; keep any developer
    # AQ_SUBSTRATE_TOOLS out of the way so these assertions are deterministic.
    monkeypatch.delenv("AQ_SUBSTRATE_TOOLS", raising=False)


def test_substrate_flag_off_is_byte_identical_to_slice2(monkeypatch):
    """AQ_AUTHORITY_ENVELOPE on, AQ_SUBSTRATE_BINDING OFF: a spends_money blast-0 step gates
    categorically (Slice-2) with NO substrate-mismatch reason -- byte-identical to pre-substrate."""
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    monkeypatch.delenv("AQ_SUBSTRATE_BINDING", raising=False)
    reasons = _reasons(_work("do the thing", spends_money=True), _inst())
    assert reasons and not _has_substrate_mismatch(reasons)


def test_both_flags_on_absent_class_surfaces_red_flag(monkeypatch):
    """RED->GREEN at the loop: both flags on, a spends_money step (structurally absent) is held with a
    SUBSTRATE MISMATCH red flag, which the off path never emits."""
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    monkeypatch.setenv("AQ_SUBSTRATE_BINDING", "1")
    reasons = _reasons(_work("do the thing", spends_money=True), _inst())
    assert _has_substrate_mismatch(reasons)


def test_both_flags_on_reachable_class_gates_without_mismatch(monkeypatch):
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    monkeypatch.setenv("AQ_SUBSTRATE_BINDING", "1")
    reasons = _reasons(_work("fire an http post to the external api"), _inst())
    assert reasons and not _has_substrate_mismatch(reasons)   # gated, but as a reachable class


def test_both_flags_on_internal_aqdb_write_stays_auto(monkeypatch):
    monkeypatch.setenv("AQ_AUTHORITY_ENVELOPE", "1")
    monkeypatch.setenv("AQ_SUBSTRATE_BINDING", "1")
    reasons = _reasons(_work("upsert the customer subscription record in aqdb"), _inst())
    assert reasons == []


def test_authority_flag_off_ignores_substrate_flag(monkeypatch):
    """AQ_AUTHORITY_ENVELOPE OFF => the envelope block never runs, so AQ_SUBSTRATE_BINDING is inert
    and the gate is byte-identical (empty)."""
    monkeypatch.delenv("AQ_AUTHORITY_ENVELOPE", raising=False)
    monkeypatch.setenv("AQ_SUBSTRATE_BINDING", "1")
    reasons = _reasons(_work("do the thing", spends_money=True), _inst())
    assert reasons == []


# ==============================================================================================
# flag helper
# ==============================================================================================
@pytest.mark.parametrize("val,want", [("1", True), ("true", True), ("on", True),
                                      ("0", False), ("", False), ("no", False)])
def test_enabled_reads_flag(val, want):
    assert enabled({"AQ_SUBSTRATE_BINDING": val}) is want


def test_enabled_default_off():
    assert enabled({}) is False
