"""Real-PostgreSQL tests for the AQ-owned governed principle lifecycle."""
from __future__ import annotations

import os
import uuid

import pytest

from management.api.principle_governance import (
    PgGovernedPrincipleLifecycle, PromotionRefused,
)

DSN = os.environ.get("AQ_GOV_TEST_DSN", "postgresql://kthomas@localhost/aq_governance_test")


def env(environment_id: str, domain: str, mission: str = "mission", harness: str = "aq-v1"):
    return {"environment_id": environment_id, "domain": domain,
            "mission_id": mission, "harness": harness}


@pytest.fixture
def gov():
    try:
        g = PgGovernedPrincipleLifecycle(DSN)
        with g._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE causal_principle_plan_outcome, causal_principle_plan_usage, "
                        "causal_principle_transition RESTART IDENTITY CASCADE")
    except Exception as exc:
        pytest.skip(f"real PostgreSQL governance test unavailable: {exc}")
    return g


def edge(name="docs"):
    return {"cause": name, "effect": "measure_up", "scope": {"tenant": "demo"}}


def mine(g, e=None):
    e = e or edge()
    g.register_mined(e, env("run-1", "documentation"), "run:1", "aq-miner")
    return e


def authorize(g, e, ref="review:1"):
    return g.promote(
        e, authorization_environment=env("control-1", "governance", "control"),
        evidence_ref=ref, applies_here=True, applies_here_how="replayed exact frozen queries",
        negative_control="reverse expected direction",
        negative_control_result="reversed oracle rejected both claims",
        adjudicated_by="independent-reviewer")


def test_shadow_first_is_bounded_and_non_authoritative(gov):
    e = mine(gov)
    guidance = gov.shadow_guidance(e)
    assert guidance == {"status": "provisional", "may_influence_bounded_experiment": True,
                        "requires_bounded_experiment": True, "authoritative": False,
                        "transition_id": 1, "promotion_transition_id": None}
    row = gov.history(e)[0]
    assert row["transition_kind"] == "mined" and row["evidence_ref"] == "run:1"


def test_aliases_for_same_context_do_not_clear_cross_environment_gate(gov):
    e = mine(gov)
    # Different caller labels, identical domain/mission/harness -> identical fingerprint.
    gov.record_environment_test(e, env("alias-a", "documentation"), "test:a", "increase", 2)
    gov.record_environment_test(e, env("alias-b", "documentation"), "test:b", "increase", 3)
    with pytest.raises(PromotionRefused, match=">=2 execution"):
        authorize(gov, e)
    assert gov.shadow_guidance(e)["status"] == "provisional"


def test_one_environment_cannot_promote(gov):
    e = mine(gov)
    gov.record_environment_test(e, env("run-2", "documentation"), "test:one", "increase", 1)
    with pytest.raises(PromotionRefused):
        authorize(gov, e)


def test_miner_cannot_authorize_own_rule(gov):
    e = mine(gov)
    gov.record_environment_test(e, env("run-2", "documentation"), "test:one", "increase", 1)
    gov.record_environment_test(e, env("run-3", "api-client", "mission-2"), "test:two", "increase", 1)
    with pytest.raises(PromotionRefused, match="independent"):
        gov.promote(e, authorization_environment=env("ctl", "governance"), evidence_ref="review:self",
                    applies_here=True, applies_here_how="query", negative_control="invert",
                    negative_control_result="failed", adjudicated_by="aq-miner")


def test_full_lifecycle_noise_does_not_demote_new_domain_refutation_does(gov):
    e = mine(gov)
    gov.record_environment_test(e, env("run-2", "documentation"), "test:one", "increase", 2)
    gov.record_environment_test(e, env("run-3", "api-client", "mission-2"), "test:two", "increase", 1)
    authorize(gov, e)
    assert gov.shadow_guidance(e)["authoritative"] is True

    # Mirror control: a small no-change observation is noise, not counter-evidence.
    noise = gov.record_environment_test(e, env("run-4", "queue", "mission-3"),
                                        "test:noise", "increase", 0.01, noise_tolerance=0.1)
    assert noise["result"] == "noise" and noise["automatic_demotion"] is False
    assert gov.shadow_guidance(e)["status"] == "promoted"

    # A refutation in an already-tested domain is logged but does not establish transfer failure.
    old = gov.record_environment_test(e, env("run-5", "documentation"),
                                      "test:old-domain", "increase", -2)
    assert old["result"] == "refutes" and old["automatic_demotion"] is False
    assert gov.shadow_guidance(e)["status"] == "promoted"

    # Counter-evidence in a genuinely new environment withdraws authority with no human call.
    demoted = gov.record_environment_test(e, env("run-6", "queue", "mission-3"),
                                           "test:refute", "increase", -3, noise_tolerance=0.1)
    assert demoted["automatic_demotion"] is True and demoted["status"] == "demoted"
    assert gov.shadow_guidance(e)["authoritative"] is False

    # Replay is idempotent and cannot append a second demotion.
    replay = gov.record_environment_test(e, env("run-6", "queue", "mission-3"),
                                         "test:refute", "increase", -3)
    assert replay["replayed"] is True
    history = gov.history(e)
    assert [r["transition_kind"] for r in history].count("demote") == 1
    assert all(r["evidence_ref"] and r["rule_version"] for r in history)


def test_transition_provenance_is_append_only_and_required_by_database(gov):
    e = mine(gov)
    import psycopg2
    # Control d: attempts to remove the evidence reference or transition itself are rejected.
    with pytest.raises(psycopg2.Error, match="append-only"):
        with gov._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE causal_principle_transition SET evidence_ref=NULL WHERE id=1")
    with pytest.raises(psycopg2.Error, match="append-only"):
        with gov._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM causal_principle_transition WHERE id=1")

    # Raw SQL cannot insert a transition with absent provenance either.
    with pytest.raises(psycopg2.Error):
        with gov._connect() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO causal_principle_transition
              (cause,effect,scope,from_status,to_status,transition_kind,environment_id,
               environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,
               evidence_result,bounded_experiment,authority_after,transitioned_by,rule_version)
              VALUES ('x','y','{}',NULL,'provisional','mined','e','d','f','m','h',NULL,
                      'mined',false,false,'miner','v1')""")


def test_pg_planning_enforces_shadow_authority_boundary(gov):
    from management.api.causal_store import PgCausalEdgeStore
    store = PgCausalEdgeStore(DSN)
    e = edge("shadow-plan")
    e.update({"formality": "fuzzy", "strictness": "advisory", "directness": "judgment",
              "executor": {"kind": "judgment"}, "predicted_certainty": 0.3})
    store.put(e)
    store.governance.register_mined(e, env("run-plan", "documentation"), "run:plan", "aq-miner")
    step = [{"action": "shadow-plan", "effect": "measure_up"}]
    ordinary = store.assess_plan(step)
    assert ordinary["covered"] == 0 and ordinary["carries_authority"] is False
    bounded = store.assess_plan(step, bounded_experiment=True)
    assert bounded["covered"] == 1 and bounded["per_step"][0]["authoritative"] is False


def test_concurrent_refutations_create_exactly_one_demotion(gov):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    e = mine(gov, edge("concurrent"))
    gov.record_environment_test(e, env("run-a", "documentation"), "test:a", "increase", 1)
    gov.record_environment_test(e, env("run-b", "api-client", "mission-b"), "test:b", "increase", 1)
    authorize(gov, e)
    barrier = threading.Barrier(2)
    def refute(n):
        barrier.wait()
        return gov.record_environment_test(
            e, env(f"run-new-{n}", f"new-domain-{n}", f"mission-{n}"),
            f"test:concurrent:{n}", "increase", -1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(refute, (1, 2)))
    assert sum(bool(r["automatic_demotion"]) for r in results) == 1
    assert [r["transition_kind"] for r in gov.history(e)].count("demote") == 1
    assert gov.shadow_guidance(e)["status"] == "demoted"


def test_repromotion_requires_fresh_post_demotion_cross_environment_evidence(gov):
    e = mine(gov, edge("reversible"))
    gov.record_environment_test(e, env("a", "documentation"), "test:a", "increase", 1)
    gov.record_environment_test(e, env("b", "api-client", "mission-b"), "test:b", "increase", 1)
    authorize(gov, e)
    gov.record_environment_test(e, env("c", "queue", "mission-c"), "test:refute", "increase", -1)
    with pytest.raises(PromotionRefused):
        authorize(gov, e, "review:stale")
    gov.record_environment_test(e, env("d", "worker", "mission-d"), "test:revalidate:d", "increase", 1)
    gov.record_environment_test(e, env("e", "scheduler", "mission-e"), "test:revalidate:e", "increase", 1)
    authorize(gov, e, "review:fresh")
    assert gov.shadow_guidance(e)["status"] == "promoted"


def test_database_rejects_provenance_shaped_authority_laundering(gov):
    import psycopg2
    with pytest.raises(psycopg2.Error, match="predecessor mismatch"):
        with gov._connect() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO causal_principle_transition
              (cause,effect,scope,from_status,to_status,transition_kind,environment_id,
               environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,
               evidence_result,bounded_experiment,authority_after,transitioned_by,adjudicated_by,
               negative_control,negative_control_result,rule_version,detail)
              VALUES ('invented','authority','{}','provisional','promoted','promote','fake-id',
                      'fake-domain','fake-fingerprint','fake-mission','fake-harness','fake-evidence',
                      'authorized',false,true,'fake','other','fake','fake','v1',
                      '{"applies_here":true,"applies_here_how":"fake"}')""")


def test_nonfinite_measurements_cannot_demote(gov):
    e = mine(gov, edge("finite"))
    with pytest.raises(Exception, match="finite"):
        gov.record_environment_test(e, env("nan", "domain"), "test:nan", "increase", float("nan"))


def test_live_record_outcome_endpoint_automatically_demotes(gov, monkeypatch):
    pytest.importorskip("fastapi")
    import importlib
    from fastapi.testclient import TestClient
    from management.api.causal_store import PgCausalEdgeStore
    appmod = importlib.import_module("management.api.app")
    store = PgCausalEdgeStore(DSN)
    e = edge("live-outcome")
    e.update({"formality": "evidential", "strictness": "advisory", "directness": "predicate",
              "executor": {"kind": "predicate", "ref": "measure.sql"}, "predicted_certainty": 0.8})
    store.put(e)
    store.governance.register_mined(e, env("mine", "documentation"), "run:mine", "aq-miner")
    store.governance.record_environment_test(e, env("a", "documentation"), "run:a", "increase", 1)
    store.governance.record_environment_test(e, env("b", "api-client", "mission-b"), "run:b", "increase", 1)
    authorize(store.governance, e)
    monkeypatch.setattr(appmod, "causal", store)
    monkeypatch.setenv("AQ_GOVERNANCE_EVIDENCE_TOKEN", "evidence-secret")
    response = TestClient(appmod.app).post("/api/causal/record-outcome", json={
        "cause": "live-outcome", "effect": "measure_up", "scope": {"tenant": "demo"},
        "predicted_certainty": 0.8, "actual_success": False,
        "environment": env("c", "queue", "mission-c"), "evidence_ref": "run:c",
        "observed_delta": -1, "expected_direction": "increase"},
        headers={"x-aq-governance-evidence-token": "evidence-secret"})
    assert response.status_code == 200, response.text
    assert response.json()["governance"]["automatic_demotion"] is True
    assert store.governance.shadow_guidance(e)["status"] == "demoted"


def _promoted_for_usefulness(gov, name="useful"):
    from management.api.principle_governance import UnproductivityPolicy
    e = mine(gov, edge(name))
    gov.record_environment_test(e, env("support-a", "documentation"), f"{name}:support:a", "increase", 1)
    gov.record_environment_test(e, env("support-b", "api-client", "mission-b"), f"{name}:support:b", "increase", 1)
    promotion_id = authorize(gov, e, f"{name}:review")
    return e, promotion_id


def test_assessment_is_not_selection_and_exact_governor_is_returned(gov):
    from management.api.causal_store import PgCausalEdgeStore
    store = PgCausalEdgeStore(DSN)
    e, promotion_id = _promoted_for_usefulness(store.governance, "assessment-only")
    e.update({"formality": "evidential", "strictness": "advisory", "directness": "predicate",
              "executor": {"kind": "predicate", "ref": "measure.sql"}, "predicted_certainty": 0.6})
    store.put(e)
    profile = store.assess_plan([{"action": "assessment-only", "effect": "measure_up"}])
    governor = profile["per_step"][0]["governor"]
    assert governor["promotion_transition_id"] == promotion_id
    assert governor["identity"] == list(store.governance.identity(e))
    with gov._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM causal_principle_plan_usage")
        assert cur.fetchone()[0] == 0, "assessment/coverage must not count as selected use"


def test_unproductivity_demotes_on_exact_threshold(gov):
    from management.api.principle_governance import UnproductivityPolicy
    e, promotion_id = _promoted_for_usefulness(gov, "never-pays")
    policy = UnproductivityPolicy(selection_threshold=3, horizon_days=14, minimum_span_days=0)
    results = []
    for n in range(3):
        plan = f"plan-{n}"
        gov.record_plan_use(e, promotion_transition_id=promotion_id, plan_id=plan,
                            goal_id="mission:measure_up", environment=env(f"use-{n}", "ops"),
                            evidence_ref=f"select:{n}")
        results.append(gov.record_plan_outcome(e, plan_id=plan, goal_reached=False,
                                               evidence_ref=f"outcome:{n}",
                                               environment=env(f"use-{n}", "ops"), policy=policy))
    assert [r["automatic_demotion"] for r in results] == [False, False, True]
    assert results[-1]["metrics"]["successful_chains"] == 0
    assert gov.history(e)[-1]["evidence_result"] == "unproductive"


def test_high_selection_working_rule_survives_unproductivity_demoter(gov):
    from management.api.principle_governance import UnproductivityPolicy
    e, promotion_id = _promoted_for_usefulness(gov, "pays-once")
    policy = UnproductivityPolicy(selection_threshold=3, horizon_days=14, minimum_span_days=0)
    for n, reached in enumerate((False, False, True, False, False, False)):
        plan = f"working-{n}"
        gov.record_plan_use(e, promotion_transition_id=promotion_id, plan_id=plan,
                            goal_id="mission:measure_up", environment=env(f"working-{n}", "ops"),
                            evidence_ref=f"working:select:{n}")
        result = gov.record_plan_outcome(e, plan_id=plan, goal_reached=reached,
                                         evidence_ref=f"working:outcome:{n}",
                                         environment=env(f"working-{n}", "ops"), policy=policy)
        assert result["automatic_demotion"] is False
    assert gov.shadow_guidance(e)["status"] == "promoted"
    assert result["metrics"]["selected"] == 6
    assert result["metrics"]["successful_chains"] == 1


def test_default_minimum_span_blocks_burst_demotions(gov):
    from management.api.principle_governance import UnproductivityPolicy
    e, promotion_id = _promoted_for_usefulness(gov, "burst")
    policy = UnproductivityPolicy()  # 5 uses / 14 days / >=1 day span
    for n in range(5):
        plan = f"burst-{n}"
        gov.record_plan_use(e, promotion_transition_id=promotion_id, plan_id=plan,
                            goal_id="mission:measure_up", environment=env(f"burst-{n}", "ops"),
                            evidence_ref=f"burst:select:{n}")
        result = gov.record_plan_outcome(e, plan_id=plan, goal_reached=False,
                                         evidence_ref=f"burst:outcome:{n}",
                                         environment=env(f"burst-{n}", "ops"), policy=policy)
    assert result["metrics"]["selected"] == 5 and result["metrics"]["span_days"] < 1
    assert result["automatic_demotion"] is False
    assert gov.shadow_guidance(e)["status"] == "promoted"


def test_unknown_or_replayed_plan_cannot_add_unproductivity_count(gov):
    from management.api.principle_governance import UnproductivityPolicy
    e, promotion_id = _promoted_for_usefulness(gov, "replay-use")
    policy = UnproductivityPolicy(selection_threshold=3, horizon_days=14, minimum_span_days=0)
    unknown = gov.record_plan_outcome(e, plan_id="missing", goal_reached=False,
                                      evidence_ref="missing:outcome", environment=env("x", "ops"), policy=policy)
    assert unknown["resolved"] is False
    gov.record_plan_use(e, promotion_transition_id=promotion_id, plan_id="one", goal_id="g",
                        environment=env("one", "ops"), evidence_ref="one:select")
    first = gov.record_plan_outcome(e, plan_id="one", goal_reached=False,
                                    evidence_ref="one:outcome", environment=env("one", "ops"), policy=policy)
    replay = gov.record_plan_outcome(e, plan_id="one", goal_reached=False,
                                     evidence_ref="one:replay", environment=env("one", "ops"), policy=policy)
    assert first["resolved"] is True and replay["resolved"] is False


def test_live_select_and_outcome_path_demotes_for_unproductivity(gov, monkeypatch):
    pytest.importorskip("fastapi")
    import importlib
    from fastapi.testclient import TestClient
    from management.api.causal_store import PgCausalEdgeStore
    appmod = importlib.import_module("management.api.app")
    store = PgCausalEdgeStore(DSN)
    e, promotion_id = _promoted_for_usefulness(store.governance, "live-unproductive")
    e.update({"formality": "evidential", "strictness": "advisory", "directness": "predicate",
              "executor": {"kind": "predicate", "ref": "measure.sql"}, "predicted_certainty": 0.6})
    store.put(e)
    monkeypatch.setattr(appmod, "causal", store)
    monkeypatch.setenv("AQ_GOVERNANCE_EVIDENCE_TOKEN", "evidence-secret")
    monkeypatch.setenv("AQ_UNPRODUCTIVE_SELECTION_THRESHOLD", "3")
    monkeypatch.setenv("AQ_UNPRODUCTIVE_HORIZON_DAYS", "14")
    monkeypatch.setenv("AQ_UNPRODUCTIVE_MINIMUM_SPAN_DAYS", "0")
    client = TestClient(appmod.app)
    results = []
    for n in range(3):
        profile = store.assess_plan([{"action": "live-unproductive", "effect": "measure_up"}])
        governor = profile["per_step"][0]["governor"]
        select = client.post("/api/causal/governance/select", json={
            "cause": "live-unproductive", "effect": "measure_up", "scope": {"tenant": "demo"},
            "promotion_transition_id": governor["promotion_transition_id"],
            "plan_id": f"live-plan-{n}", "goal_id": "mission:measure_up",
            "environment": env(f"live-{n}", "ops"), "evidence_ref": f"live:select:{n}"},
            headers={"x-aq-governance-evidence-token": "evidence-secret"})
        assert select.status_code == 200, select.text
        outcome = client.post("/api/causal/record-outcome", json={
            "cause": "live-unproductive", "effect": "measure_up", "scope": {"tenant": "demo"},
            "predicted_certainty": 0.6, "actual_success": False,
            "environment": env(f"live-{n}", "ops"), "evidence_ref": f"live:outcome:{n}",
            "observed_delta": 0, "expected_direction": "increase",
            "plan_id": f"live-plan-{n}", "goal_reached": False},
            headers={"x-aq-governance-evidence-token": "evidence-secret"})
        assert outcome.status_code == 200, outcome.text
        results.append(outcome.json()["governance"])
    assert [r["automatic_demotion"] for r in results] == [False, False, True]
    assert results[-1]["trigger"] == "unproductivity"
    assert store.governance.shadow_guidance(e)["status"] == "demoted"


def test_unproductivity_policy_defaults_and_validation():
    from management.api.principle_governance import GovernanceError, UnproductivityPolicy
    policy = UnproductivityPolicy.from_env({})
    assert (policy.selection_threshold, policy.horizon_days, policy.minimum_span_days) == (5, 14, 1)
    with pytest.raises(GovernanceError):
        UnproductivityPolicy.from_env({"AQ_UNPRODUCTIVE_SELECTION_THRESHOLD": "2"})
    with pytest.raises(GovernanceError):
        UnproductivityPolicy.from_env({"AQ_UNPRODUCTIVE_HORIZON_DAYS": "7",
                                       "AQ_UNPRODUCTIVE_MINIMUM_SPAN_DAYS": "8"})


def test_concurrent_unproductive_threshold_creates_one_demotion(gov):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from management.api.principle_governance import UnproductivityPolicy
    e, promotion_id = _promoted_for_usefulness(gov, "concurrent-unproductive")
    policy = UnproductivityPolicy(selection_threshold=3, horizon_days=14, minimum_span_days=0)
    for n in range(4):
        gov.record_plan_use(e, promotion_transition_id=promotion_id, plan_id=f"cp-{n}", goal_id="g",
                            environment=env(f"cp-{n}", "ops"), evidence_ref=f"cp:select:{n}")
    for n in range(2):
        gov.record_plan_outcome(e, plan_id=f"cp-{n}", goal_reached=False,
                                evidence_ref=f"cp:outcome:{n}", environment=env(f"cp-{n}", "ops"), policy=policy)
    barrier = threading.Barrier(2)
    def finish(n):
        barrier.wait()
        return gov.record_plan_outcome(e, plan_id=f"cp-{n}", goal_reached=False,
                                       evidence_ref=f"cp:outcome:{n}",
                                       environment=env(f"cp-{n}", "ops"), policy=policy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finish, (2, 3)))
    assert sum(bool(r["automatic_demotion"]) for r in results) == 1
    assert [r["transition_kind"] for r in gov.history(e)].count("demote") == 1


def test_outcomes_outside_horizon_do_not_count(gov):
    from management.api.principle_governance import UnproductivityPolicy
    e, promotion_id = _promoted_for_usefulness(gov, "horizon")
    ident = gov.identity(e)
    with gov._connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO causal_principle_plan_usage
          (cause,effect,scope,promotion_transition_id,plan_id,goal_id,selected,governed,
           environment_id,environment_domain,environment_fingerprint,selection_evidence_ref,selected_at)
          VALUES (%s,%s,%s,%s,'old-plan','g',true,true,'old','ops','old-fp','old:select',now()-interval '15 days')
          RETURNING id""", (*ident, promotion_id))
        old_id = cur.fetchone()[0]
        cur.execute("INSERT INTO causal_principle_plan_outcome(usage_id,goal_reached,outcome_evidence_ref,resolved_at) "
                    "VALUES (%s,false,'old:outcome',now()-interval '15 days')", (old_id,))
    policy = UnproductivityPolicy(selection_threshold=3, horizon_days=14, minimum_span_days=0)
    for n in range(2):
        gov.record_plan_use(e, promotion_transition_id=promotion_id, plan_id=f"new-{n}", goal_id="g",
                            environment=env(f"new-{n}", "ops"), evidence_ref=f"new:select:{n}")
        result = gov.record_plan_outcome(e, plan_id=f"new-{n}", goal_reached=False,
                                         evidence_ref=f"new:outcome:{n}",
                                         environment=env(f"new-{n}", "ops"), policy=policy)
    assert result["metrics"]["selected"] == 2
    assert result["automatic_demotion"] is False
