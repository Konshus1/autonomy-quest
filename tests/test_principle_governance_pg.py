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
            cur.execute("TRUNCATE causal_principle_transition RESTART IDENTITY")
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
                        "max_experiments": 1, "authoritative": False, "transition_id": 1}
    row = gov.history(e)[0]
    assert row["transition_kind"] == "mined" and row["evidence_ref"] == "run:1"


def test_aliases_for_same_context_do_not_clear_cross_environment_gate(gov):
    e = mine(gov)
    # Different caller labels, identical domain/mission/harness -> identical fingerprint.
    gov.record_environment_test(e, env("alias-a", "documentation"), "test:a", "increase", 2)
    gov.record_environment_test(e, env("alias-b", "documentation"), "test:b", "increase", 3)
    with pytest.raises(PromotionRefused, match=">=2 environment"):
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
