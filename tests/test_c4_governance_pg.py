"""C4 PostgreSQL controls: actor proposals never grant flagship planning authority."""
from __future__ import annotations
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import psycopg2
import pytest

from management.api.principle_governance import GovernanceError, PgGovernedPrincipleLifecycle, PromotionRefused
from runner.db import Db, Work

DSN = os.environ.get("AQ_C4_TEST_DSN")
ACTOR_DSN = os.environ.get("AQ_C4_ACTOR_DSN")
LOOP_DSN = os.environ.get("AQ_C4_LOOP_DSN")
GOVERNANCE_DSN = os.environ.get("AQ_C4_GOVERNANCE_DSN")
EVALUATOR_DSN = os.environ.get("AQ_C4_EVALUATOR_DSN")
_REQUIRED_DSNS = (DSN, ACTOR_DSN, LOOP_DSN, GOVERNANCE_DSN, EVALUATOR_DSN)
pytestmark = pytest.mark.skipif(
    not all(_REQUIRED_DSNS), reason="set all five AQ_C4 principal DSNs for C4 controls"
)


def _tag():
    return "c4_" + uuid.uuid4().hex


def _completed_run(cur, tag):
    cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) "
                "VALUES (%s,'c4','c4','running','p',%s::jsonb) RETURNING id",
                (tag, '{"steps":[]}'))
    work_id = cur.fetchone()[0]
    cur.execute("INSERT INTO runs(work_id,completed_at,outcome,succeeded) "
                "VALUES (%s,now(),'evidence',true) RETURNING id", (work_id,))
    return work_id, cur.fetchone()[0]


def test_provisional_edge_is_not_known_and_incomplete_or_dangling_evidence_is_rejected():
    tag = _tag()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO work(kind,summary,rationale,status) VALUES (%s,'c4','c4','running') RETURNING id", (tag,))
        wid = cur.fetchone()[0]
        cur.execute("INSERT INTO runs(work_id) VALUES (%s) RETURNING id", (wid,))
        rid = cur.fetchone()[0]
        with pytest.raises(psycopg2.Error, match="missing or incomplete"):
            with conn.cursor() as bad:
                bad.execute("INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,scope_conditions,evidence_run_ids) VALUES (%s,'measure_up','m','toward','{}',ARRAY[%s]::bigint[])", (tag, rid))
        conn.rollback()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        _, rid = _completed_run(cur, tag)
        cur.execute("INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,scope_conditions,predicted_certainty,evidence_run_ids) VALUES (%s,'measure_up','m','toward','{}',.95,ARRAY[%s]::bigint[])", (tag, rid))
        cur.execute("SELECT to_status,authority_after FROM causal_principle_transition WHERE cause=%s ORDER BY id DESC LIMIT 1", (tag,))
        assert cur.fetchone() == ("provisional", False)
    db = Db(LOOP_DSN, graph="none", connect_retries=1)
    assert not [r for r in db.known_plan_relations() if r["source_action"] == tag]


def test_independently_grounded_positive_path_requires_manual_promotion():
    tag = _tag()
    proposal={"source_action":tag,"direct_effect":"measure_up","mission_measure":"m",
      "direction":"toward","mechanism":"organic checked acquisition","scope":[],
      "certainty":.95,"evidence":"completed acquisition receipt"}
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        plan={"steps":[{"step_id":"s1","action":tag,"expected_effect":"measure_up","scope":{}}]}
        cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) VALUES (%s,'organic-grounding','checked acquisition','running','p',%s::jsonb) RETURNING id",(tag,json.dumps(plan)))
        work_id=cur.fetchone()[0]
        cur.execute("INSERT INTO plan_acquisition(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction,status) VALUES (%s,'p','s1','search',1,'a','acquire relation','completed') RETURNING acquisition_id",(work_id,))
        acquisition_id=cur.fetchone()[0]
        cur.execute("INSERT INTO runs(work_id,completed_at,outcome,succeeded) VALUES (%s,now(),'acquired organic relation',true) RETURNING id",(work_id,))
        rid=cur.fetchone()[0]
        cur.execute("UPDATE plan_acquisition SET result=%s::jsonb,completed_at=now() WHERE acquisition_id=%s",(json.dumps({"causal_proposals":[proposal],"_aq_completion_run_id":rid}),acquisition_id))
    evaluator = PgGovernedPrincipleLifecycle(EVALUATOR_DSN, init_schema=False)
    edge_id=evaluator.attest_acquisition(acquisition_id,rid,0)
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM causal_edge WHERE edge_id=%s AND scope_conditions::text='{}'",(edge_id,))
        assert cur.fetchone()[0]==1
    db = Db(LOOP_DSN, graph="none", connect_retries=1)
    edge = (tag, "measure_up", "{}")
    promoter = PgGovernedPrincipleLifecycle(GOVERNANCE_DSN, init_schema=False)
    contexts = []
    for index, domain in enumerate(("docs", "api", "authorization"), 1):
        contexts.append(evaluator.register_execution_context(
            {"instance_id": f"urn:uuid:00000000-0000-4000-8000-{index:012d}",
             "environment_id": f"execution-{index}", "domain": domain,
             "mission_id": f"mission-{index}", "harness": "c4/v1"},
            {"source": "c4-independent-evaluator", "receipt": f"context-{index}"}))
    for index in (0, 1):
        result = evaluator.attest_grounding_observation(
            edge, context_id=contexts[index], test_kind="support",
            evidence_ref=f"support-{index}", expected_direction="increase",
            observed_delta=1.0, noise_tolerance=0.1,
            evidence_payload={"source": "frozen-evaluator", "receipt": f"support-{index}"})
        assert result["observation_id"]
    with pytest.raises(psycopg2.errors.RaiseException, match="applicability"):
        promoter.promote_grounded(
            edge, authorization_context_id=contexts[2], review_evidence_ref="manual-review")
    evaluator.attest_grounding_observation(
        edge, context_id=contexts[2], test_kind="applicability",
        evidence_ref="applies-here", expected_direction="increase", observed_delta=1.0,
        noise_tolerance=0.1, evidence_payload={"source": "frozen-evaluator",
        "receipt": "applicability", "method": "exact action/effect/scope replay"})
    with pytest.raises(psycopg2.errors.RaiseException, match="negative control"):
        promoter.promote_grounded(
            edge, authorization_context_id=contexts[2], review_evidence_ref="manual-review")
    evaluator.attest_grounding_observation(
        edge, context_id=contexts[2], test_kind="negative_control",
        evidence_ref="negative-control", expected_direction="increase", observed_delta=0.0,
        noise_tolerance=0.1, evidence_payload={"source": "frozen-evaluator",
        "receipt": "negative-control", "method": "reversed intervention stayed inert"})
    transition_id = promoter.promote_grounded(
        edge, authorization_context_id=contexts[2], review_evidence_ref="manual-review")
    rows = [r for r in db.known_plan_relations() if r["source_action"] == tag]
    assert len(rows) == 1 and rows[0]["promotion_transition_id"] == transition_id
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT cardinality(support_observation_ids),promoted_by FROM "
            "aq_control.grounding_promotion WHERE promotion_transition_id=%s",
            (transition_id,),
        )
        assert cur.fetchone() == (2, "aq_governance")
        cur.execute(
            "SELECT count(*) FROM aq_control.grounding_promotion_support "
            "WHERE promotion_transition_id=%s",
            (transition_id,),
        )
        assert cur.fetchone()[0] == 2
        cur.execute(
            "SELECT count(*),min(evaluator_principal),max(evaluator_principal) FROM "
            "aq_control.grounding_observation WHERE cause=%s",
            (tag,),
        )
        assert cur.fetchone() == (4, "aq_evaluator", "aq_evaluator")


def test_failed_cycle_transaction_leaves_neither_edge_nor_transition():
    tag = _tag()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        _, rid = _completed_run(cur, tag)
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,scope_conditions,evidence_run_ids) VALUES (%s,'measure_up','m','toward','{}',ARRAY[%s]::bigint[])", (tag, rid))
        conn.rollback()
    finally:
        conn.close()
    with psycopg2.connect(DSN) as check, check.cursor() as cur:
        cur.execute("SELECT count(*) FROM causal_edge WHERE source_action=%s", (tag,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM causal_principle_transition WHERE cause=%s", (tag,))
        assert cur.fetchone()[0] == 0


def test_acquisition_stages_only_after_governance_attests_completed_receipt():
    tag = _tag()
    plan = {"steps": [{"step_id": "s1", "action": tag, "expected_effect": "measure_up"}]}
    proposal = {
        "source_action": tag,
        "direct_effect": "measure_up",
        "mission_measure": "m",
        "direction": "toward",
        "mechanism": "candidate only",
        "scope": [],
        "certainty": .95,
        "evidence": "durable run receipt",
    }
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO work(kind,summary,rationale,status,plan_id,plan) "
            "VALUES (%s,'c4','c4','running','p',%s::jsonb) RETURNING id",
            (tag, json.dumps(plan)),
        )
        work_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO plan_acquisition(work_id,plan_id,target_step_id,rung,rung_index,"
            "action_step_id,instruction,status) "
            "VALUES (%s,'p','s1','search',1,'a1','search','running') RETURNING acquisition_id",
            (work_id,),
        )
        acquisition_id = cur.fetchone()[0]
        cur.execute("INSERT INTO runs(work_id) VALUES (%s) RETURNING id", (work_id,))
        run_id = cur.fetchone()[0]
    db = Db(LOOP_DSN, graph="none", connect_retries=1)
    work = Work(
        id=work_id,
        kind="knowledge_acquisition:search",
        summary="search",
        rationale="c4",
        plan_id="p",
        plan=plan,
        acquisition_id=acquisition_id,
    )
    with db.tx() as cur:
        cur.execute(
            "UPDATE runs SET completed_at=now(),outcome='acquired',succeeded=true WHERE id=%s",
            (run_id,),
        )
        assert db.stage_acquired_relations(cur, run_id, work, [proposal]) == []
        db.complete_acquisition(
            cur,
            acquisition_id,
            {"causal_proposals": [proposal], "_aq_completion_run_id": run_id},
        )
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM causal_edge WHERE source_action=%s", (tag,))
        assert cur.fetchone()[0] == 0
    evaluator = PgGovernedPrincipleLifecycle(EVALUATOR_DSN, init_schema=False)
    edge_id = evaluator.attest_acquisition(acquisition_id, run_id, 0)
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT event_id FROM aq_control.evidence_event "
            "WHERE acquisition_id=%s AND run_id=%s",
            (acquisition_id, run_id),
        )
        event_id = cur.fetchone()[0]
    with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT public.stage_flagship_causal_proposal("
            "%s,%s,%s,'measure_up','m','toward','candidate only','{}',.95,"
            "'durable run receipt')",
            (run_id, acquisition_id, tag),
        )
        edge_id = cur.fetchone()[0]
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT evidence_run_ids FROM causal_edge WHERE edge_id=%s", (edge_id,))
        assert cur.fetchone()[0] == [run_id]
        cur.execute(
            "SELECT to_status,authority_after,evidence_ref FROM causal_principle_transition "
            "WHERE cause=%s",
            (tag,),
        )
        status, authority, evidence_ref = cur.fetchone()
        assert (status, authority) == ("provisional", False)
        assert evidence_ref == f"governance-event:{event_id}"
        cur.execute(
            "SELECT application_kind,edge_id FROM aq_control.evidence_application "
            "WHERE event_id=%s",
            (str(event_id),),
        )
        assert cur.fetchone() == ("stage_edge", edge_id)
    assert not [r for r in db.known_plan_relations() if r["source_action"] == tag]


def test_acquisition_stage_rollback_removes_run_completion_edge_and_transition():
    tag = _tag()
    plan = {"steps": [{"step_id": "s1", "action": tag, "expected_effect": "measure_up"}]}
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) VALUES (%s,'c4','c4','running','p',%s::jsonb) RETURNING id", (tag, __import__('json').dumps(plan)))
        wid = cur.fetchone()[0]
        cur.execute("INSERT INTO plan_acquisition(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction,status) VALUES (%s,'p','s1','search',1,'a1','search','running') RETURNING acquisition_id", (wid,))
        aid = cur.fetchone()[0]
        cur.execute("INSERT INTO runs(work_id) VALUES (%s) RETURNING id", (wid,))
        rid = cur.fetchone()[0]
    db = Db(LOOP_DSN, graph="none", connect_retries=1)
    work = Work(id=wid, kind="knowledge_acquisition:search", summary="search", rationale="c4", plan_id="p", plan=plan, acquisition_id=aid)
    proposal = {"source_action": tag, "direct_effect": "measure_up", "mission_measure": "m", "direction": "toward", "mechanism": "candidate", "scope": [], "certainty": .8, "evidence": "run"}
    with pytest.raises(RuntimeError, match="fault after staging"):
        with db.tx() as cur:
            cur.execute("UPDATE runs SET completed_at=now(),outcome='acquired',succeeded=true WHERE id=%s", (rid,))
            db.stage_acquired_relations(cur, rid, work, [proposal])
            raise RuntimeError("fault after staging")
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT completed_at FROM runs WHERE id=%s", (rid,))
        assert cur.fetchone()[0] is None
        cur.execute("SELECT count(*) FROM causal_edge WHERE source_action=%s", (tag,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM causal_principle_transition WHERE cause=%s", (tag,))
        assert cur.fetchone()[0] == 0


def test_actor_database_principal_gets_permission_errors_at_both_authority_writes():
    with psycopg2.connect(ACTOR_DSN) as conn, conn.cursor() as cur:
        # MATCH THE EXACT OBJECT, NOT JUST "permission denied".
        # This read match="permission denied" and passed for the WRONG REASON: after GRANT INSERT
        # ON causal_edge TO aq_actor — a realistic partial misconfiguration — the actor is stopped
        # only by "permission denied for SEQUENCE causal_edge_edge_id_seq". The boundary had been
        # genuinely weakened and this control still went green. Verified by doing exactly that
        # grant against exact Compose. Pin the table so any grant of table authority goes red.
        with pytest.raises(psycopg2.errors.InsufficientPrivilege, match="permission denied for table causal_edge"):
            cur.execute("INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,scope_conditions,evidence_run_ids) VALUES ('actor_forge','measure_up','m','toward','{}',ARRAY[1]::bigint[])")
        conn.rollback()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege, match="permission denied for table causal_principle_transition"):
            cur.execute("INSERT INTO causal_principle_transition(cause,effect,scope,to_status,transition_kind,environment_id,environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,evidence_result,authority_after,transitioned_by,rule_version) VALUES ('actor_forge','measure_up','{}','provisional','mined','e','d','f','m','h','r','mined',false,'actor','v')")
        conn.rollback()
    if LOOP_DSN != DSN:
        with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege, match="permission denied for table causal_edge"):
                cur.execute("INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,scope_conditions,evidence_run_ids) VALUES ('loop_forge','measure_up','m','toward','{}',ARRAY[1]::bigint[])")
            conn.rollback()
            with pytest.raises(psycopg2.errors.InsufficientPrivilege, match="permission denied for table causal_principle_transition"):
                cur.execute("INSERT INTO causal_principle_transition(cause,effect,scope,to_status,transition_kind,environment_id,environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,evidence_result,authority_after,transitioned_by,rule_version) VALUES ('loop_forge','measure_up','{}','provisional','mined','e','d','f','m','h','r','mined',false,'loop','v')")
            conn.rollback()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM causal_edge WHERE source_action IN ('actor_forge','loop_forge')")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM causal_principle_transition WHERE cause='actor_forge'")
        assert cur.fetchone()[0] == 0


def test_c4_principal_dsns_are_exact_and_share_one_server():
    expected = {
        "owner": (DSN, "aq_owner"),
        "actor": (ACTOR_DSN, "aq_actor"),
        "loop": (LOOP_DSN, "aq_loop"),
        "governance": (GOVERNANCE_DSN, "aq_governance"),
        "evaluator": (EVALUATOR_DSN, "aq_evaluator"),
    }
    identities = {}
    for role, (dsn, expected_user) in expected.items():
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT current_user,current_database(),inet_server_addr()::text,"
                "inet_server_port(),pg_postmaster_start_time()::text,"
                "current_setting('server_version_num')"
            )
            row = cur.fetchone()
        assert row[0] == expected_user, (role, row[0])
        assert row[1] == "aq", (role, row[1])
        identities[role] = row[1:]
    assert len(set(identities.values())) == 1, identities


def test_runtime_principals_cannot_write_exact_trusted_evidence_table():
    for dsn in (LOOP_DSN, GOVERNANCE_DSN, EVALUATOR_DSN):
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            with pytest.raises(
                psycopg2.errors.InsufficientPrivilege,
                match="permission denied for table evidence_event",
            ):
                # Explicit UUID avoids the false green where only a sequence is denied.
                cur.execute(
                    "INSERT INTO aq_control.evidence_event("
                    "event_id,event_kind,subject_key,run_id,work_id,acquisition_id,"
                    "payload,payload_digest) "
                    "VALUES (%s,'acquisition_completed','forged',1,1,1,'{}','forged')",
                    (str(uuid.uuid4()),),
                )
            conn.rollback()


# M2 fail-first boundary controls. The operational loop may mutate ordinary runtime rows, but
# those rows must not be sufficient to authorize a causal stage or resolution.
def test_loop_forged_runtime_rows_cannot_authorize_flagship_stage():
    assert LOOP_DSN != DSN, "C4 rig must supply a distinct aq_loop principal"
    tag = _tag()
    plan = {"steps": [{"step_id": "s1", "action": tag, "expected_effect": "measure_up"}]}
    with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO work(kind,summary,rationale,status,plan_id,plan) "
            "VALUES (%s,'forged','forged','running','p',%s::jsonb) RETURNING id",
            (tag, json.dumps(plan)),
        )
        work_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO plan_acquisition(work_id,plan_id,target_step_id,rung,rung_index,"
            "action_step_id,instruction,status) "
            "VALUES (%s,'p','s1','search',1,'a1','forged','running') RETURNING acquisition_id",
            (work_id,),
        )
        acquisition_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO runs(work_id,completed_at,outcome,succeeded) "
            "VALUES (%s,now(),'forged',true) RETURNING id",
            (work_id,),
        )
        run_id = cur.fetchone()[0]
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="trusted acquisition evidence event is missing",
        ):
            cur.execute(
                "SELECT stage_flagship_causal_proposal(%s,%s,%s,'measure_up','m','toward',"
                "'forged mechanism','{}',.9,'forged evidence')",
                (run_id, acquisition_id, tag),
            )
        conn.rollback()


def test_loop_forged_resolution_rows_cannot_authorize_edge_mutation():
    assert LOOP_DSN != DSN, "C4 rig must supply a distinct aq_loop principal"
    tag = _tag()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        work_id, run_id = _completed_run(cur, tag)
        cur.execute(
            "INSERT INTO causal_edge(source_action,direct_effect,mission_measure,"
            "relation_direction,scope_conditions,predicted_certainty,evidence_run_ids) "
            "VALUES (%s,'measure_up','m','toward','{}',.9,ARRAY[%s]::bigint[]) "
            "RETURNING edge_id",
            (tag, run_id),
        )
        edge_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO planning_prediction(edge_id,plan_id,work_id,step_id) "
            "VALUES (%s,'p',%s,'s1') RETURNING prediction_id",
            (edge_id, work_id),
        )
        prediction_id = cur.fetchone()[0]
    with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE planning_prediction SET resolved_run_id=%s,executed=true,"
            "direction_confirmed=true WHERE prediction_id=%s",
            (run_id, prediction_id),
        )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="trusted prediction resolution event is missing",
        ):
            cur.execute(
                "SELECT resolve_flagship_causal_edge(%s,%s,%s,true)",
                (edge_id, run_id, prediction_id),
            )
        conn.rollback()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT support_count,falsified_by FROM causal_edge WHERE edge_id=%s",
            (edge_id,),
        )
        assert cur.fetchone() == (0, None)


def test_trusted_resolution_derives_false_rejects_mismatch_and_replay_is_one_shot():
    tag = _tag()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        work_id, run_id = _completed_run(cur, tag)
        cur.execute(
            "INSERT INTO causal_edge(source_action,direct_effect,mission_measure,"
            "relation_direction,scope_conditions,predicted_certainty,evidence_run_ids) "
            "VALUES (%s,'measure_up','m','toward','{}',.9,ARRAY[%s]::bigint[]) "
            "RETURNING edge_id",
            (tag, run_id),
        )
        edge_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO planning_prediction(edge_id,plan_id,work_id,step_id) "
            "VALUES (%s,'p',%s,'s1') RETURNING prediction_id",
            (edge_id, work_id),
        )
        prediction_id = cur.fetchone()[0]
    with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE planning_prediction SET resolved_run_id=%s,executed=true,"
            "direct_effect_confirmed=true,direction_confirmed=false,"
            "outcome_kind='direction_refuted',outcome_evidence='trusted observation',"
            "resolved_at=now() WHERE prediction_id=%s",
            (run_id, prediction_id),
        )
        cur.execute(
            "INSERT INTO principle_support_event("
            "prediction_id,run_id,edge_id,delta,outcome_kind,evidence) "
            "VALUES (%s,%s,%s,-1,'direction_refuted','trusted observation')",
            (prediction_id, run_id, edge_id),
        )
    evaluator = PgGovernedPrincipleLifecycle(EVALUATOR_DSN, init_schema=False)
    assert evaluator.attest_prediction_resolution(prediction_id, run_id) == edge_id
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT event_id FROM aq_control.evidence_event "
            "WHERE prediction_id=%s AND run_id=%s",
            (prediction_id, run_id),
        )
        event_id = cur.fetchone()[0]
    with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="caller confirmation does not match trusted prediction resolution event",
        ):
            cur.execute(
                "SELECT public.resolve_flagship_causal_edge(%s,%s,%s,true)",
                (edge_id, run_id, prediction_id),
            )
        conn.rollback()
    for _ in range(2):
        with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT public.resolve_flagship_causal_edge(%s,%s,%s,false)",
                (edge_id, run_id, prediction_id),
            )
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT support_count,falsified_by FROM causal_edge WHERE edge_id=%s",
            (edge_id,),
        )
        assert cur.fetchone() == (0, run_id)
        cur.execute(
            "SELECT application_kind,edge_id,applied_delta FROM aq_control.evidence_application "
            "WHERE event_id=%s",
            (str(event_id),),
        )
        assert cur.fetchone() == ("resolve_edge", edge_id, -1)


def test_governance_runtime_schema_init_preserves_migrated_boundary():
    # The service constructs this class at runtime. Existing-table IF NOT EXISTS paths must work
    # without CREATE privilege, and runtime DDL must not overwrite the trusted function owner/ACL.
    PgGovernedPrincipleLifecycle(GOVERNANCE_DSN, init_schema=True)


def test_bridge_catalog_pins_owner_safe_path_and_exact_execute_acl():
    functions = {
        "public.validate_causal_principle_transition_insert()": (False, False),
        "public.validate_causal_edge_run_evidence()": (False, False),
        "public.register_flagship_causal_proposal()": (False, False),
        "public.stage_flagship_causal_proposal(bigint,bigint,text,text,text,text,text,text,real,text)": (True, False),
        "public.resolve_flagship_causal_edge(bigint,bigint,bigint,boolean)": (True, False),
        "aq_control.attest_acquisition_evidence(bigint,bigint,integer)": (False, False),
        "aq_control.attest_prediction_resolution(bigint,bigint)": (False, False),
        "aq_control.register_execution_context(text,text,text,text,text,jsonb)": (False, False),
        "aq_control.attest_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb)": (False, False),
        "aq_control.attest_and_apply_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb)": (False, False),
        "aq_control.promote_grounded_principle(text,text,text,uuid,text)": (False, True),
        "aq_control.attest_and_stage_acquisition(bigint,bigint,integer)": (False, False),
        "aq_control.attest_and_apply_prediction(bigint,bigint)": (False, False),
        "aq_control.register_mined_principle(text,text,text,bigint[],bigint[])": (True, False),
        "aq_control.attest_and_apply_plan_outcome(bigint,jsonb)": (False, False),
        "aq_control.pending_plan_outcome_runs(integer)": (False, False),
        "aq_control.quarantine_plan_outcome(bigint,text)": (False, False),
        "aq_control.require_checked_run_outcome_event()": (False, False),
        "aq_control.enqueue_checked_run_outcome_event()": (False, False),
    }
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        for signature, (loop_execute, governance_execute) in functions.items():
            cur.execute(
                """
                SELECT r.rolname,p.prosecdef,p.proconfig,
                       has_function_privilege('aq_loop',p.oid,'EXECUTE'),
                       has_function_privilege('aq_actor',p.oid,'EXECUTE'),
                       has_function_privilege('aq_governance',p.oid,'EXECUTE'),
                       has_function_privilege('aq_evaluator',p.oid,'EXECUTE'),
                       EXISTS (
                         SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                          WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
                  FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                 WHERE p.oid=%s::regprocedure
                """,
                (signature,),
            )
            owner, security_definer, settings, loop_acl, actor_acl, gov_acl, evaluator_acl, public_acl = cur.fetchone()
            assert owner == "aq_control_owner", signature
            assert security_definer is True, signature
            assert settings == ["search_path=pg_catalog, pg_temp"], (signature, settings)
            assert loop_acl is loop_execute, signature
            assert actor_acl is False, signature
            assert gov_acl is governance_execute, signature
            expected_evaluator = signature in {
                "aq_control.register_execution_context(text,text,text,text,text,jsonb)",
                "aq_control.attest_and_apply_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb)",
                "aq_control.attest_and_stage_acquisition(bigint,bigint,integer)",
                "aq_control.attest_and_apply_prediction(bigint,bigint)",
                "aq_control.attest_and_apply_plan_outcome(bigint,jsonb)",
                "aq_control.pending_plan_outcome_runs(integer)",
                "aq_control.quarantine_plan_outcome(bigint,text)",
            }
            assert evaluator_acl is expected_evaluator, signature
            assert public_acl is False, signature
        cur.execute("""SELECT grantee,table_name,privilege_type FROM information_schema.role_table_grants
          WHERE grantee IN ('aq_loop','aq_actor','aq_governance','aq_evaluator')
            AND table_schema='aq_control' AND table_name='plan_outcome_observation'
            AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')""")
        assert cur.fetchall()==[]


def test_governance_cannot_directly_forge_grounding_transition():
    tag = _tag()
    with psycopg2.connect(GOVERNANCE_DSN) as conn, conn.cursor() as cur:
        with pytest.raises(
            psycopg2.errors.InsufficientPrivilege,
            match="permission denied for table causal_principle_transition",
        ):
            cur.execute(
                """
                INSERT INTO public.causal_principle_transition(
                  cause,effect,scope,from_status,to_status,transition_kind,
                  environment_id,environment_domain,environment_fingerprint,
                  mission_id,harness,evidence_ref,evidence_result,bounded_experiment,
                  authority_after,transitioned_by,rule_version,automatic,detail)
                VALUES (%s,'measure_up','{}',NULL,'provisional','mined',
                  'forged-execution','forged-domain','forged-fingerprint',
                  'forged-mission','forged-harness','forged-evidence','mined',false,
                  false,'aq_governance','aq-governed-principle-v1',false,'{}')
                """,
                (tag,),
            )
        conn.rollback()


def test_independent_evaluator_grounding_catalog_is_present():
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname='aq_evaluator'")
        assert cur.fetchone() == (True,)
        cur.execute(
            "SELECT to_regclass('aq_control.execution_context'),"
            "to_regclass('aq_control.grounding_observation'),"
            "to_regclass('aq_control.grounding_promotion'),"
            "to_regclass('aq_control.grounding_promotion_support')"
        )
        assert all(value is not None for value in cur.fetchone())
        signatures = (
            "aq_control.register_execution_context(text,text,text,text,text,jsonb)",
            "aq_control.attest_and_apply_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb)",
            "aq_control.promote_grounded_principle(text,text,text,uuid,text)",
        )
        for signature in signatures:
            cur.execute("SELECT to_regprocedure(%s)", (signature,))
            assert cur.fetchone()[0] is not None, signature


def test_promoter_cannot_execute_evaluator_grounding_writer():
    with psycopg2.connect(GOVERNANCE_DSN) as conn, conn.cursor() as cur:
        with pytest.raises(
            psycopg2.errors.InsufficientPrivilege,
            match="permission denied for function attest_and_apply_grounding_observation",
        ):
            cur.execute(
                "SELECT aq_control.attest_and_apply_grounding_observation("
                "%s,'cause','effect','{}','support','evidence','increase',1.0,0.0,'{}')",
                (str(uuid.uuid4()),),
            )
        conn.rollback()


def test_execution_context_identity_is_immutable_and_promoter_cannot_alias_it():
    evaluator = PgGovernedPrincipleLifecycle(EVALUATOR_DSN, init_schema=False)
    instance = "urn:uuid:" + str(uuid.uuid4())
    base_environment = {"instance_id": instance, "environment_id": "execution-one",
                        "domain": "docs", "mission_id": "mission", "harness": "c4/v1"}
    attestation = {"source": "frozen-evaluator", "receipt": "execution-one"}
    context_id = evaluator.register_execution_context(base_environment, attestation)
    assert evaluator.register_execution_context(base_environment, attestation) == context_id
    changed = dict(base_environment, domain="invented-alias")
    with pytest.raises(
        psycopg2.errors.RaiseException,
        match="execution identity is already attested with different content",
    ):
        evaluator.register_execution_context(changed, attestation)
    aliased = dict(changed, environment_id="execution-alias")
    with pytest.raises(
        psycopg2.errors.RaiseException,
        match="execution attestation is already bound to a different context",
    ):
        evaluator.register_execution_context(aliased, attestation)


def test_evaluator_and_promoter_execute_acls_are_mutually_exclusive():
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT has_function_privilege('aq_evaluator',"
            "'aq_control.promote_grounded_principle(text,text,text,uuid,text)', 'EXECUTE'),"
            "has_function_privilege('aq_governance',"
            "'aq_control.attest_and_apply_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb)','EXECUTE'),"
            "has_function_privilege('aq_evaluator',"
            "'aq_control.attest_and_apply_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb)','EXECUTE'),"
            "has_function_privilege('aq_governance',"
            "'aq_control.promote_grounded_principle(text,text,text,uuid,text)','EXECUTE')"
        )
        assert cur.fetchone() == (False, False, True, True)
    with psycopg2.connect(EVALUATOR_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_user")
        assert cur.fetchone()[0] == "aq_evaluator"
        with pytest.raises(
            psycopg2.errors.InsufficientPrivilege,
            match="permission denied for function promote_grounded_principle",
        ):
            cur.execute(
                "SELECT aq_control.promote_grounded_principle("
                "'cause','effect','{}',%s::uuid,'forged-review')",
                (str(uuid.uuid4()),),
            )
        conn.rollback()


def test_grounding_rows_are_immutable_to_evaluator_and_promoter():
    evaluator = PgGovernedPrincipleLifecycle(EVALUATOR_DSN, init_schema=False)
    instance = "urn:uuid:" + str(uuid.uuid4())
    context_id = evaluator.register_execution_context(
        {"instance_id": instance, "environment_id": "immutability", "domain": "docs",
         "mission_id": "mission", "harness": "c4/v1"},
        {"source": "frozen-evaluator", "receipt": "immutability"})
    for dsn in (EVALUATOR_DSN, GOVERNANCE_DSN):
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            with pytest.raises(
                psycopg2.errors.InsufficientPrivilege,
                match="permission denied for table execution_context",
            ):
                cur.execute(
                    "UPDATE aq_control.execution_context SET domain='forged' "
                    "WHERE context_id=%s::uuid",
                    (context_id,),
                )
            conn.rollback()


def test_multiple_acquisition_proposals_select_their_exact_events():
    tag = _tag()
    proposals = [
        {"source_action": tag, "direct_effect": "measure_up", "mission_measure": "m",
         "direction": "toward", "mechanism": "first", "scope": [{"key":"variant","value":"one"}],
         "certainty": .8, "evidence": "first receipt"},
        {"source_action": tag, "direct_effect": "measure_up", "mission_measure": "m",
         "direction": "toward", "mechanism": "second", "scope": [{"key":"variant","value":"two"}],
         "certainty": .7, "evidence": "second receipt"},
    ]
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        plan = {"steps":[{"step_id":"s1","action":tag,"expected_effect":"measure_up"}]}
        cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) VALUES (%s,'c4','c4','running','p',%s::jsonb) RETURNING id", (tag,json.dumps(plan)))
        work_id=cur.fetchone()[0]
        cur.execute("INSERT INTO plan_acquisition(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction,status) VALUES (%s,'p','s1','search',1,'a','search','completed') RETURNING acquisition_id",(work_id,))
        acquisition_id=cur.fetchone()[0]
        cur.execute("INSERT INTO runs(work_id,completed_at,outcome,succeeded) VALUES (%s,now(),'acquired',true) RETURNING id",(work_id,))
        run_id=cur.fetchone()[0]
        cur.execute("UPDATE plan_acquisition SET result=%s::jsonb,completed_at=now() WHERE acquisition_id=%s",(json.dumps({"causal_proposals":proposals,"_aq_completion_run_id":run_id}),acquisition_id))
    evaluator=PgGovernedPrincipleLifecycle(EVALUATOR_DSN,init_schema=False)
    edge_ids=[evaluator.attest_acquisition(acquisition_id,run_id,index) for index in range(2)]
    assert len(set(edge_ids))==2
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM aq_control.evidence_application a JOIN aq_control.evidence_event e USING(event_id) WHERE e.acquisition_id=%s",(acquisition_id,))
        assert cur.fetchone()[0]==2


def test_acquisition_event_rejects_semantically_conflicting_existing_identity():
    tag = _tag()
    evaluator=PgGovernedPrincipleLifecycle(EVALUATOR_DSN,init_schema=False)
    def make(direction, evidence):
        proposal={"source_action":tag,"direct_effect":"measure_up","mission_measure":"m",
                  "direction":direction,"mechanism":"same","scope":[],"certainty":.8,"evidence":evidence}
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            plan={"steps":[{"step_id":"s1","action":tag,"expected_effect":"measure_up"}]}
            cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) VALUES (%s,'c4','c4','running','p',%s::jsonb) RETURNING id",(tag,json.dumps(plan)))
            work_id=cur.fetchone()[0]
            cur.execute("INSERT INTO plan_acquisition(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction,status) VALUES (%s,'p','s1','search',1,'a','search','completed') RETURNING acquisition_id",(work_id,))
            acquisition_id=cur.fetchone()[0]
            cur.execute("INSERT INTO runs(work_id,completed_at,outcome,succeeded) VALUES (%s,now(),'acquired',true) RETURNING id",(work_id,))
            run_id=cur.fetchone()[0]
            cur.execute("UPDATE plan_acquisition SET result=%s::jsonb,completed_at=now() WHERE acquisition_id=%s",(json.dumps({"causal_proposals":[proposal],"_aq_completion_run_id":run_id}),acquisition_id))
        return acquisition_id,run_id
    first=make("toward","first")
    edge_id=evaluator.attest_acquisition(*first,0)
    second=make("away","contradiction")
    with pytest.raises(psycopg2.errors.RaiseException,match="conflicts with an existing edge identity"):
        evaluator.attest_acquisition(*second,0)
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT relation_direction FROM causal_edge WHERE edge_id=%s",(edge_id,))
        assert cur.fetchone()==("toward",)


def test_prediction_attestation_requires_support_outcome_and_evidence_equality():
    tag=_tag()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        work_id,run_id=_completed_run(cur,tag)
        cur.execute("INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,scope_conditions,predicted_certainty,evidence_run_ids) VALUES (%s,'measure_up','m','toward','{}',.9,ARRAY[%s]::bigint[]) RETURNING edge_id",(tag,run_id))
        edge_id=cur.fetchone()[0]
        cur.execute("INSERT INTO planning_prediction(edge_id,plan_id,work_id,step_id,resolved_run_id,executed,direction_confirmed,outcome_kind,outcome_evidence,resolved_at) VALUES (%s,'p',%s,'s1',%s,true,true,'confirmed','prediction receipt',now()) RETURNING prediction_id",(edge_id,work_id,run_id))
        prediction_id=cur.fetchone()[0]
    with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO principle_support_event(prediction_id,run_id,edge_id,delta,outcome_kind,evidence) VALUES (%s,%s,%s,1,'confirmed','different support receipt')",(prediction_id,run_id,edge_id))
    evaluator=PgGovernedPrincipleLifecycle(EVALUATOR_DSN,init_schema=False)
    with pytest.raises(psycopg2.errors.RaiseException,match="one exact completed resolution/support event"):
        evaluator.attest_prediction_resolution(prediction_id,run_id)
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM aq_control.evidence_event WHERE prediction_id=%s",(prediction_id,))
        assert cur.fetchone()[0]==0


def _owner_fixture_promoted_edge(tag):
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        work_id,run_id=_completed_run(cur,tag)
        cur.execute("INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,scope_conditions,predicted_certainty,evidence_run_ids) VALUES (%s,'measure_up','m','toward','{}',.9,ARRAY[%s]::bigint[]) RETURNING edge_id",(tag,run_id))
        cur.execute("SELECT id FROM causal_principle_transition WHERE cause=%s ORDER BY id LIMIT 1",(tag,))
        mined_id=cur.fetchone()[0]
        for index in (1,2):
            cur.execute("INSERT INTO causal_principle_transition(cause,effect,scope,from_status,to_status,transition_kind,environment_id,environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,evidence_result,expected_direction,observed_delta,bounded_experiment,authority_after,transitioned_by,rule_version,automatic,detail) VALUES (%s,'measure_up','{}','provisional','provisional','shadow_test',%s,%s,%s,'m','h',%s,'supports','increase',1,true,false,'fixture-evaluator','fixture-v1',false,'{}')",(tag,f'e{index}',f'd{index}',f'f{index}',f'support:{index}'))
        cur.execute("INSERT INTO causal_principle_transition(cause,effect,scope,from_status,to_status,transition_kind,environment_id,environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,evidence_result,bounded_experiment,authority_after,transitioned_by,adjudicated_by,negative_control,negative_control_result,rule_version,automatic,detail) VALUES (%s,'measure_up','{}','provisional','promoted','promote','auth','auth','auth','m','h','manual','authorized',false,true,'fixture-promoter','independent','nc','passed','fixture-v1',false,%s::jsonb) RETURNING id",(tag,json.dumps({'applies_here':True,'applies_here_how':'fixture'})))
        promotion_id=cur.fetchone()[0]
    return work_id,promotion_id


def _bind_authorization_work(work_id,global_plan_id,plan):
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("UPDATE work SET plan_id=%s,plan=%s::jsonb WHERE id=%s",
                    (global_plan_id,json.dumps(plan),work_id))


def _new_fixture_work(tag):
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        work_id,_=_completed_run(cur,tag)
    return work_id


def test_pre_act_authorization_catalog_is_narrow_and_owned():
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('aq_control.plan_authorization'),to_regclass('public.plan_governance_defer_outbox'),to_regprocedure('aq_control.authorize_plan(text,bigint,jsonb)')")
        assert all(value is not None for value in cur.fetchone())
        cur.execute("SELECT r.rolname,p.prosecdef,p.proconfig,has_function_privilege('aq_governance',p.oid,'EXECUTE'),has_function_privilege('aq_loop',p.oid,'EXECUTE') FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner WHERE p.oid='aq_control.authorize_plan(text,bigint,jsonb)'::regprocedure")
        assert cur.fetchone()==('aq_control_owner',True,['search_path=pg_catalog, pg_temp'],True,False)
        cur.execute("SELECT has_table_privilege('aq_governance','causal_principle_plan_usage','INSERT'),has_table_privilege('aq_governance','causal_principle_plan_outcome','INSERT'),has_table_privilege('aq_governance','aq_control.plan_authorization','INSERT'),has_table_privilege('aq_loop','plan_governance_defer_outbox','INSERT'),has_table_privilege('aq_loop','plan_governance_defer_outbox','UPDATE')")
        assert cur.fetchone()==(False,False,False,True,False)


def test_pre_act_authorization_derives_block_and_false_receipt():
    tag=_tag();work_id,promotion_id=_owner_fixture_promoted_edge(tag)
    global_plan_id=f"urn:uuid:{uuid.uuid4()}/plan/{uuid.uuid4()}"
    plan={'steps':[{'step_id':'s1','action':tag,'expected_effect':'measure_up','expected_direction':'away','scope':{}}]}
    _bind_authorization_work(work_id,global_plan_id,plan)
    with psycopg2.connect(GOVERNANCE_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT aq_control.authorize_plan(%s,%s,%s::jsonb)",(global_plan_id,work_id,json.dumps(plan)))
        receipt=cur.fetchone()[0]
    assert receipt['disposition']=='block' and receipt['may_act'] is False
    assert receipt['selected'] is False and receipt['governed'] is False
    assert str(promotion_id) in receipt['reason']


def test_pre_act_authorization_allow_and_abstain_are_honest_and_replay_safe():
    tag=_tag();first_work,promotion_id=_owner_fixture_promoted_edge(tag)
    cases=[
      (first_work,'toward','allow',True,True),
      (_new_fixture_work(_tag()),'neutral','block',False,False),
    ]
    for work_id,direction,expected,selected,governed in cases:
        global_plan_id=f"urn:uuid:{uuid.uuid4()}/plan/{uuid.uuid4()}"
        plan={'steps':[{'step_id':'s1','action':tag,'expected_effect':'measure_up','expected_direction':direction,'scope':{}}]}
        _bind_authorization_work(work_id,global_plan_id,plan)
        with psycopg2.connect(GOVERNANCE_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT aq_control.authorize_plan(%s,%s,%s::jsonb)",(global_plan_id,work_id,json.dumps(plan)))
            row=cur.fetchone()[0]
        assert (row['disposition'],row['selected'],row['governed'])==(expected,selected,governed)
    abstain_work=_new_fixture_work(_tag())
    abstain_id=f"urn:uuid:{uuid.uuid4()}/plan/{uuid.uuid4()}"
    plan={'steps':[{'step_id':'s1','action':_tag(),'expected_effect':'measure_up','expected_direction':'toward','scope':{}}]}
    _bind_authorization_work(abstain_work,abstain_id,plan)
    with psycopg2.connect(GOVERNANCE_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT aq_control.authorize_plan(%s,%s,%s::jsonb)",(abstain_id,abstain_work,json.dumps(plan)))
        first=cur.fetchone()[0]
        cur.execute("SELECT aq_control.authorize_plan(%s,%s,%s::jsonb)",(abstain_id,abstain_work,json.dumps(plan)))
        assert cur.fetchone()[0]==first
    assert first['disposition']=='abstain' and first['may_act'] is True
    assert first['selected'] is False and first['governed'] is False


def test_checked_mining_derives_provisional_transition_as_loop():
    tag=_tag()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        work_id,run_id=_completed_run(cur,tag)
        cur.execute("UPDATE runs SET productive=true,measure_before=0,measure_after=1 WHERE id=%s",(run_id,))
        cur.execute("INSERT INTO learnings(run_id,insight,evidence,scope,confidence,evidence_kind) VALUES (%s,'derived mining','receipt','local',.8,'actor_claim') RETURNING id",(run_id,))
        learning_id=cur.fetchone()[0]
    edge={'cause':tag,'effect':'measure_up','scope':{},'formality':'fuzzy','strictness':'advisory','directness':'judgment','executor':{'kind':'judgment'},'predicted_certainty':.34,'mined':True,'evidence_run_ids':[run_id],'provenance':[{'run_id':run_id,'work_id':work_id,'learning_id':learning_id,'insight':'derived mining'}]}
    with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO ralph_causal_edges(cause,effect,scope,edge) VALUES (%s,'measure_up','{}',%s::jsonb)",(tag,json.dumps(edge)))
        cur.execute("SELECT aq_control.register_mined_principle(%s,'measure_up','{}',%s::bigint[],%s::bigint[])",(tag,[run_id],[learning_id]))
        transition_id=cur.fetchone()[0]
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_status,authority_after FROM causal_principle_transition WHERE id=%s",(transition_id,))
        assert cur.fetchone()==('provisional',False)
        cur.execute("SELECT relation_direction,evidence_run_ids FROM causal_edge WHERE source_action=%s",(tag,))
        assert cur.fetchone()==('toward',[run_id])


def test_evaluator_checked_refutation_automatically_withdraws_promotion():
    tag=_tag();_,promotion_id=_owner_fixture_promoted_edge(tag)
    evaluator=PgGovernedPrincipleLifecycle(EVALUATOR_DSN,init_schema=False)
    context=evaluator.register_execution_context(
      {'instance_id':f'urn:uuid:{uuid.uuid4()}','environment_id':'new-domain-refutation','domain':_tag(),'mission_id':'m','harness':'m5/v1'},
      {'source':'independent-evaluator','receipt':_tag()})
    payload={'source':'independent-evaluator','receipt':_tag()}
    statement="SELECT aq_control.attest_and_apply_grounding_observation(%s::uuid,%s,'measure_up','{}','support','post-promotion-refutation','increase',-2,.1,%s::jsonb)"
    with psycopg2.connect(EVALUATOR_DSN) as conn, conn.cursor() as cur:
        cur.execute(statement,(context,tag,json.dumps(payload)))
        result=cur.fetchone()[0]
        cur.execute(statement,(context,tag,json.dumps(payload)))
        replay=cur.fetchone()[0]
    assert result['automatic_demotion'] is True and result['status']=='demoted'
    assert replay['replayed'] is True and replay['historical_demotion'] is True
    assert replay['automatic_demotion'] is False and replay['status']=='demoted'
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT from_status,to_status,transition_kind,automatic FROM causal_principle_transition WHERE cause=%s ORDER BY id DESC LIMIT 1",(tag,))
        assert cur.fetchone()==('promoted','demoted','demote',True)
        cur.execute("SELECT count(*) FROM aq_control.grounding_demotion WHERE promotion_transition_id=%s",(promotion_id,))
        assert cur.fetchone()[0]==1


def test_evaluator_derived_plan_outcomes_demote_sustained_unproductivity():
    tag=_tag();_,promotion_id=_owner_fixture_promoted_edge(tag)
    run_ids=[]
    for index in range(5):
        global_plan_id=f"urn:uuid:{uuid.uuid4()}/plan/{uuid.uuid4()}"
        plan={'goal_predicate':{'metric':'mission_delta','operator':'>=','value':1},'steps':[{'step_id':'s1','action':tag,'expected_effect':'measure_up','expected_direction':'toward','scope':{}}]}
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) VALUES (%s,'m5','m5','running',%s,%s::jsonb) RETURNING id",(tag,global_plan_id,json.dumps(plan)))
            work_id=cur.fetchone()[0]
        with psycopg2.connect(GOVERNANCE_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT aq_control.authorize_plan(%s,%s,%s::jsonb)",(global_plan_id,work_id,json.dumps(plan)))
            authorization=cur.fetchone()[0]
            assert authorization['disposition']=='allow'
        completed=f"{4-index} days"
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO runs(work_id,plan_authorization_id,completed_at,outcome,succeeded,productive,measure_before,measure_after) VALUES (%s,%s::uuid,now()-(%s)::interval,'done',true,false,0,0) RETURNING id",(work_id,authorization['authorization_id'],completed))
            run_id=cur.fetchone()[0];run_ids.append(run_id)
            cur.execute("INSERT INTO plan_evaluation(run_id,work_id,plan_id,goal_satisfied,steps_executed,steps_confirmed,intent_satisfied,observed_metrics,step_results,evaluated_at) VALUES (%s,%s,%s,false,1,0,true,'{}','[]',now()-(%s)::interval)",(run_id,work_id,global_plan_id,completed))
            cur.execute("INSERT INTO plan_outcome_evaluation_outbox(run_id) VALUES (%s) ON CONFLICT(run_id) DO NOTHING",(run_id,))
        evaluator_evidence={'source':'independent-evaluator-fixture',
          'eligible':True,'goal_reached':False,
          'observed_at':(datetime.now(timezone.utc)-timedelta(days=4-index)).isoformat()}
        lifecycle=PgGovernedPrincipleLifecycle(EVALUATOR_DSN,init_schema=False)
        lifecycle._independent_plan_outcome_evidence=lambda _: {
          **evaluator_evidence,'receipt':f'outcome:{run_id}:{uuid.uuid4()}'}
        def consume_outcome():
            return lifecycle.attest_plan_outcome(run_id)
        if index==0:
            with ThreadPoolExecutor(max_workers=2) as pool:
                concurrent=list(pool.map(lambda _: consume_outcome(),range(2)))
            assert all(row['resolved'] for row in concurrent)
            assert sum(bool(row.get('replayed')) for row in concurrent)==1
            with psycopg2.connect(LOOP_DSN) as loop_conn, loop_conn.cursor() as loop_cur:
                loop_cur.execute("UPDATE plan_evaluation SET goal_satisfied=true WHERE run_id=%s",(run_id,))
                loop_cur.execute("UPDATE runs SET completed_at=now()+interval '30 days' WHERE id=%s",(run_id,))
            replay_after_mutation=consume_outcome()
            assert replay_after_mutation['replayed'] is True
            with psycopg2.connect(DSN) as owner_conn, owner_conn.cursor() as owner_cur:
                owner_cur.execute("SELECT goal_reached,evidence_payload FROM aq_control.plan_outcome_observation WHERE run_id=%s",(run_id,))
                trusted_goal,trusted_payload=owner_cur.fetchone()
            assert trusted_goal is False
            assert trusted_payload['source']==evaluator_evidence['source']
            assert trusted_payload['observed_at']==evaluator_evidence['observed_at']
            result=concurrent[0]
        else:
            result=consume_outcome()
    assert result['automatic_demotion'] is True and result['status']=='demoted'
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*),bool_or(goal_reached) FROM aq_control.plan_authorization_outcome WHERE promotion_transition_id=%s",(promotion_id,))
        assert cur.fetchone()==(5,False)
        cur.execute("SELECT evidence_result,automatic FROM causal_principle_transition WHERE cause=%s ORDER BY id DESC LIMIT 1",(tag,))
        assert cur.fetchone()==('unproductive',True)



def test_plan_outcome_backlog_quarantines_poison_and_continues():
    tag=_tag();_,promotion_id=_owner_fixture_promoted_edge(tag)
    queued=[]
    for index in range(3):
        global_plan_id=f"urn:uuid:{uuid.uuid4()}/plan/{uuid.uuid4()}"
        plan={'goal_predicate':{'metric':'mission_value','operator':'>=','value':1},'steps':[{'step_id':'s1','action':tag,'expected_effect':'measure_up','expected_direction':'toward','scope':{}}]}
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) VALUES (%s,'backlog','backlog','running',%s,%s::jsonb) RETURNING id",(tag,global_plan_id,json.dumps(plan)))
            work_id=cur.fetchone()[0]
        with psycopg2.connect(GOVERNANCE_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT aq_control.authorize_plan(%s,%s,%s::jsonb)",(global_plan_id,work_id,json.dumps(plan)))
            authorization=cur.fetchone()[0]
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO runs(work_id,plan_authorization_id,completed_at,outcome,succeeded) VALUES (%s,%s::uuid,now(),'done',true) RETURNING id",(work_id,authorization['authorization_id']))
            run_id=cur.fetchone()[0]; queued.append(run_id)
            if index>0:
                cur.execute("INSERT INTO plan_evaluation(run_id,work_id,plan_id,goal_satisfied,steps_executed,steps_confirmed,intent_satisfied,observed_metrics,step_results) VALUES (%s,%s,%s,false,1,0,true,'{}','[]')",(run_id,work_id,global_plan_id))
            cur.execute("INSERT INTO plan_outcome_evaluation_outbox(run_id) VALUES (%s) ON CONFLICT(run_id) DO NOTHING",(run_id,))
    lifecycle=PgGovernedPrincipleLifecycle(EVALUATOR_DSN,init_schema=False)
    def evidence(run_id):
        if run_id==queued[1]:
            raise GovernanceError("temporary independent measure outage")
        return {'source':'independent-evaluator-backlog-fixture','receipt':f'backlog:{run_id}:{uuid.uuid4()}',
          'eligible':True,'goal_reached':False,'observed_at':datetime.now(timezone.utc).isoformat()}
    lifecycle._independent_plan_outcome_evidence=evidence
    batch=lifecycle.consume_pending_plan_outcomes(100)
    assert batch['attempted']==3 and batch['consumed']==2
    assert [row['status'] for row in batch['results']]==['quarantined','pending','promoted']
    # Fresh UUID/time production evidence still replays the first immutable application.
    replay=lifecycle.attest_plan_outcome(queued[2])
    assert replay['replayed'] is True
    lifecycle._independent_plan_outcome_evidence=lambda run_id: {
      'source':'independent-evaluator-backlog-fixture','receipt':f'retry:{run_id}:{uuid.uuid4()}',
      'eligible':True,'goal_reached':False,'observed_at':datetime.now(timezone.utc).isoformat()}
    retry=lifecycle.consume_pending_plan_outcomes(100)
    assert retry['consumed']==1 and retry['results'][0]['run_id']==queued[1]
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM (SELECT result->>'status' status FROM aq_control.plan_outcome_application WHERE run_id=%s) s",(queued[0],))
        assert cur.fetchone()[0]=='quarantined'
        cur.execute("SELECT count(*) FROM aq_control.plan_authorization_outcome WHERE run_id=%s AND promotion_transition_id=%s",(queued[2],promotion_id))
        assert cur.fetchone()[0]==1

def test_plan_outcome_outbox_rejects_crosswired_authorization_and_plan():
    tag=_tag();_,_=_owner_fixture_promoted_edge(tag)
    plan={'goal_predicate':{'metric':'mission_delta','operator':'>=','value':1},'steps':[{'step_id':'s1','action':tag,'expected_effect':'measure_up','expected_direction':'toward','scope':{}}]}
    auth_plan=f"urn:uuid:{uuid.uuid4()}/plan/{uuid.uuid4()}"
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) VALUES (%s,'auth','auth','running',%s,%s::jsonb) RETURNING id",(tag,auth_plan,json.dumps(plan)))
        auth_work=cur.fetchone()[0]
    with psycopg2.connect(GOVERNANCE_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT aq_control.authorize_plan(%s,%s,%s::jsonb)",(auth_plan,auth_work,json.dumps(plan)))
        authorization=cur.fetchone()[0]
    other_plan=f"urn:uuid:{uuid.uuid4()}/plan/{uuid.uuid4()}"
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) VALUES (%s,'other','other','running',%s,%s::jsonb) RETURNING id",(tag,other_plan,json.dumps(plan)))
        other_work=cur.fetchone()[0]
        cur.execute("INSERT INTO runs(work_id,plan_authorization_id) VALUES (%s,%s::uuid) RETURNING id",(other_work,authorization['authorization_id']))
        run_id=cur.fetchone()[0]
        cur.execute("INSERT INTO plan_evaluation(run_id,work_id,plan_id,goal_satisfied,steps_executed,steps_confirmed,intent_satisfied,observed_metrics,step_results) VALUES (%s,%s,%s,false,1,0,true,'{}','[]')",(run_id,other_work,other_plan))
        with pytest.raises(psycopg2.errors.RaiseException,match='completed run, checked authorization'):
            with conn.cursor() as bad:
                bad.execute("UPDATE runs SET completed_at=now(),outcome='crosswired',succeeded=false WHERE id=%s",(run_id,))
        conn.rollback()
    with psycopg2.connect(DSN) as verify_conn, verify_conn.cursor() as verify_cur:
        verify_cur.execute("SELECT (SELECT count(*) FROM aq_control.plan_outcome_observation WHERE run_id=%s),(SELECT count(*) FROM aq_control.plan_authorization_outcome WHERE run_id=%s),(SELECT count(*) FROM aq_control.plan_outcome_application WHERE run_id=%s)",(run_id,run_id,run_id))
        assert verify_cur.fetchone()==(0,0,0)
    # Failure/interrupt-style completion paths cannot omit the event: the owner trigger enqueues it.
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO runs(work_id,plan_authorization_id) VALUES (%s,%s::uuid) RETURNING id",(auth_work,authorization['authorization_id']))
        failed_run=cur.fetchone()[0]
    with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
        cur.execute("UPDATE runs SET completed_at=now(),outcome='failed before ACT',succeeded=false WHERE id=%s",(failed_run,))
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_outcome_evaluation_outbox WHERE run_id=%s",(failed_run,))
        assert cur.fetchone()[0]==1
