"""C4 PostgreSQL controls: actor proposals never grant flagship planning authority."""
from __future__ import annotations
import os
import uuid
from types import SimpleNamespace

import psycopg2
import pytest

from management.api.principle_governance import PgGovernedPrincipleLifecycle, PromotionRefused
from runner.db import Db, Work

DSN = os.environ.get("AQ_C4_TEST_DSN")
ACTOR_DSN = os.environ.get("AQ_C4_ACTOR_DSN")
LOOP_DSN = os.environ.get("AQ_C4_LOOP_DSN") or DSN
GOVERNANCE_DSN = os.environ.get("AQ_C4_GOVERNANCE_DSN") or DSN
pytestmark = pytest.mark.skipif(not DSN, reason="set AQ_C4_TEST_DSN for C4 PostgreSQL controls")


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


def test_00_clean_seed_contains_structure_not_claims_or_customers():
    with psycopg2.connect(DSN) as conn,conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ralph_frame_dimensions WHERE status='active'")
        assert cur.fetchone()[0]==21
        for table in ("causal_edge","causal_principle_transition","customers","subscriptions"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0]==0


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


def test_same_or_next_cycle_low_blast_unlock_requires_independent_promotion():
    tag = _tag()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        _, rid = _completed_run(cur, tag)
        cur.execute("INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,scope_conditions,predicted_certainty,evidence_run_ids) VALUES (%s,'measure_up','m','toward','{}',.95,ARRAY[%s]::bigint[])", (tag, rid))
    db = Db(LOOP_DSN, graph="none", connect_retries=1)
    assert not [r for r in db.known_plan_relations() if r["source_action"] == tag]
    lifecycle = PgGovernedPrincipleLifecycle(GOVERNANCE_DSN, init_schema=False)
    edge = (tag, "measure_up", "{}")
    with pytest.raises(PromotionRefused):
        lifecycle.promote(edge, authorization_environment={"environment_id":"auth","domain":"auth","mission_id":"m","harness":"h"}, evidence_ref="auth-before-tests", applies_here=True, applies_here_how="negative control", negative_control="low blast stayed blocked", negative_control_result="passed", adjudicated_by="independent")
    lifecycle.record_environment_test(edge, {"environment_id":"e1","domain":"d1","mission_id":"m1","harness":"h1"}, "support-1", "increase", 1.0)
    lifecycle.record_environment_test(edge, {"environment_id":"e2","domain":"d2","mission_id":"m2","harness":"h2"}, "support-2", "increase", 1.0)
    lifecycle.promote(edge, authorization_environment={"environment_id":"auth","domain":"auth","mission_id":"m","harness":"h"}, evidence_ref="auth-after-tests", applies_here=True, applies_here_how="exact action/effect/scope", negative_control="low blast stayed blocked while provisional", negative_control_result="passed", adjudicated_by="independent")
    rows = [r for r in db.known_plan_relations() if r["source_action"] == tag]
    assert len(rows) == 1 and rows[0]["promotion_transition_id"]
    lifecycle.record_environment_test(edge, {"environment_id":"e3","domain":"d3","mission_id":"m3","harness":"h3"}, "refute-3", "increase", -1.0)
    assert not [r for r in db.known_plan_relations() if r["source_action"] == tag]


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


def _acquisition_evidence(tag):
    plan = {"steps": [{"step_id": "s1", "action": tag, "expected_effect": "measure_up"}]}
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) VALUES (%s,'c4','c4','running','p',%s::jsonb) RETURNING id", (tag, __import__('json').dumps(plan)))
        wid = cur.fetchone()[0]
        cur.execute("INSERT INTO plan_acquisition(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction,status,result) VALUES (%s,'p','s1','search',1,'a1','search','completed','{}') RETURNING acquisition_id", (wid,))
        aid = cur.fetchone()[0]
        cur.execute("INSERT INTO runs(work_id,acquisition_id,completed_at,outcome,succeeded,evidence) VALUES (%s,%s,now(),'acquired',true,'independent durable run receipt') RETURNING id", (wid,aid))
        return wid,aid,cur.fetchone()[0]


def _stage_as_governance(cur, tag, aid, rid):
    cur.execute("SELECT stage_flagship_causal_proposal(%s,%s,%s,'measure_up','m','toward','candidate','{}',.95,'persisted proposal evidence')", (rid,aid,tag))
    return cur.fetchone()[0]


def test_governance_stages_exact_candidate_with_durable_event():
    if not GOVERNANCE_DSN:
        pytest.fail("AQ_C4_GOVERNANCE_DSN is mandatory for the C4 gate")
    tag = _tag(); _, aid, rid = _acquisition_evidence(tag)
    with psycopg2.connect(GOVERNANCE_DSN) as conn, conn.cursor() as cur:
        edge_id = _stage_as_governance(cur, tag, aid, rid)
    db = Db(LOOP_DSN, graph="none", connect_retries=1)
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT evidence_run_ids FROM causal_edge WHERE edge_id=%s", (edge_id,))
        assert cur.fetchone()[0] == [rid]
        cur.execute("SELECT evidence FROM flagship_causal_proposal_event WHERE edge_id=%s", (edge_id,))
        assert cur.fetchone()[0] == "persisted proposal evidence"
        cur.execute("SELECT to_status,authority_after,evidence_ref FROM causal_principle_transition WHERE cause=%s", (tag,))
        status, authority, evidence_ref = cur.fetchone()
        assert (status, authority) == ("provisional", False)
        assert evidence_ref.startswith(f"run:{rid}/")
    assert not [r for r in db.known_plan_relations() if r["source_action"] == tag]


def test_governance_stage_rollback_leaves_neither_edge_transition_nor_event():
    tag = _tag(); _, aid, rid = _acquisition_evidence(tag)
    conn = psycopg2.connect(GOVERNANCE_DSN)
    try:
        cur = conn.cursor(); _stage_as_governance(cur, tag, aid, rid); conn.rollback()
    finally:
        conn.close()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        for table,column in [("causal_edge","source_action"),("causal_principle_transition","cause")]:
            cur.execute(f"SELECT count(*) FROM {table} WHERE {column}=%s", (tag,))
            assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM flagship_causal_proposal_event WHERE run_id=%s", (rid,))
        assert cur.fetchone()[0] == 0


def test_loop_principal_cannot_call_authority_bridges_or_mutate_events():
    tag = _tag(); _, aid, rid = _acquisition_evidence(tag)
    with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            _stage_as_governance(cur, tag, aid, rid)
        conn.rollback()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("INSERT INTO flagship_causal_proposal_event(edge_id,run_id,acquisition_id,work_id,action,effect,scope,direction,evidence) VALUES (1,%s,%s,1,'x','y','{}','toward','forge')", (rid,aid))
        conn.rollback()


def test_actor_database_principal_gets_permission_errors_at_both_authority_writes():
    if not ACTOR_DSN:
        pytest.skip("set AQ_C4_ACTOR_DSN for principal isolation control")
    with psycopg2.connect(ACTOR_DSN) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege, match="permission denied"):
            cur.execute("INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,scope_conditions,evidence_run_ids) VALUES ('actor_forge','measure_up','m','toward','{}',ARRAY[1]::bigint[])")
        conn.rollback()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege, match="permission denied"):
            cur.execute("INSERT INTO causal_principle_transition(cause,effect,scope,to_status,transition_kind,environment_id,environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,evidence_result,authority_after,transitioned_by,rule_version) VALUES ('actor_forge','measure_up','{}','provisional','mined','e','d','f','m','h','r','mined',false,'actor','v')")
        conn.rollback()
    if LOOP_DSN != DSN:
        with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege, match="permission denied"):
                cur.execute("INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,scope_conditions,evidence_run_ids) VALUES ('loop_forge','measure_up','m','toward','{}',ARRAY[1]::bigint[])")
            conn.rollback()
            with pytest.raises(psycopg2.errors.InsufficientPrivilege, match="permission denied"):
                cur.execute("INSERT INTO causal_principle_transition(cause,effect,scope,to_status,transition_kind,environment_id,environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,evidence_result,authority_after,transitioned_by,rule_version) VALUES ('loop_forge','measure_up','{}','provisional','mined','e','d','f','m','h','r','mined',false,'loop','v')")
            conn.rollback()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM causal_edge WHERE source_action IN ('actor_forge','loop_forge')")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM causal_principle_transition WHERE cause='actor_forge'")
        assert cur.fetchone()[0] == 0


def test_pending_reflection_keeps_exact_pending_acquisition_and_recovers_atomically():
    tag = _tag()
    plan = {"steps": [{"step_id": "s1", "action": tag, "expected_effect": "measure_up"}]}
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) VALUES (%s,'recover','recover','running','p',%s::jsonb) RETURNING id", (tag, __import__('json').dumps(plan)))
        wid = cur.fetchone()[0]
        cur.execute("INSERT INTO plan_acquisition(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction,status) VALUES (%s,'p','s1','recall',0,'a1','recall','pending') RETURNING acquisition_id", (wid,))
        aid = cur.fetchone()[0]
        cur.execute("INSERT INTO runs(work_id,acquisition_id,outcome,succeeded,evidence) VALUES (%s,%s,'acted',true,'durable receipt') RETURNING id", (wid, aid))
        rid = cur.fetchone()[0]
    db = Db(LOOP_DSN, graph="none", connect_retries=1)
    pending = db.pending_reflection()
    assert pending["id"] == rid and pending["acquisition_id"] == aid
    from runner.executor import Usage
    with db.tx() as cur:
        db.complete_run(cur, rid, outcome="acted", succeeded=True, usage=Usage(),
                        productive=False, evidence="durable receipt",
                        measure_before=0, measure_after=0, work_status="pending")
        db.recover_acquisition(cur, aid, rid, {"recovered_reflection": True})
        db.write_learning(cur, rid, "recovered honestly", "run receipt", "local", .5)
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT completed_at IS NOT NULL FROM runs WHERE id=%s", (rid,))
        assert cur.fetchone()[0] is True
        cur.execute("SELECT status FROM plan_acquisition WHERE acquisition_id=%s", (aid,))
        assert cur.fetchone()[0] == "completed"
        cur.execute("SELECT status FROM work WHERE id=%s", (wid,))
        assert cur.fetchone()[0] == "pending"
        cur.execute("SELECT count(*) FROM learnings WHERE run_id=%s", (rid,))
        assert cur.fetchone()[0] == 1


def test_run_acquisition_identity_rejects_cross_work_and_is_immutable():
    tag = _tag()
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO work(kind,summary,rationale,status) VALUES (%s,'a','a','pending'),(%s,'b','b','pending') RETURNING id", (tag+'a', tag+'b'))
        w1, w2 = [row[0] for row in cur.fetchall()]
        cur.execute("INSERT INTO plan_acquisition(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction,status) VALUES (%s,'p','s','recall',0,'a','i','pending') RETURNING acquisition_id", (w1,))
        aid = cur.fetchone()[0]
        with pytest.raises(psycopg2.Error, match="does not belong"):
            cur.execute("INSERT INTO runs(work_id,acquisition_id) VALUES (%s,%s)", (w2, aid))
        conn.rollback()


def test_actor_step_claim_cannot_increment_edge_support_and_evidence_is_not_orphanable():
    tag = _tag(); wid, aid, rid = _acquisition_evidence(tag)
    with psycopg2.connect(GOVERNANCE_DSN) as conn, conn.cursor() as cur:
        edge_id = _stage_as_governance(cur, tag, aid, rid)
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO planning_prediction(edge_id,plan_id,work_id,step_id,step_index,source_action,expected_direct_effect,expected_direction,scope,edge_state,sufficient,predicted_certainty,resolved_run_id,executed,direct_effect_confirmed,direction_confirmed,outcome_kind,outcome_evidence) VALUES (%s,'p',%s,'s1',0,%s,'measure_up','toward','{}','solid',true,.8,%s,true,true,true,'supported','actor says confirmed') RETURNING prediction_id", (edge_id,wid,tag,rid))
        pid = cur.fetchone()[0]
    with psycopg2.connect(GOVERNANCE_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regprocedure('resolve_flagship_causal_edge(bigint,bigint,bigint,boolean)')")
        assert cur.fetchone()[0] is None
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT support_count,falsified_by FROM causal_edge WHERE edge_id=%s", (edge_id,))
        assert cur.fetchone() == (0,None)
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            cur.execute("DELETE FROM runs WHERE id=%s", (rid,))
        conn.rollback()


def test_flagship_writer_http_requires_token_and_derives_resolution():
    tag = _tag(); wid, aid, rid = _acquisition_evidence(tag)
    from fastapi.testclient import TestClient
    from management.api.app import app
    client = TestClient(app)
    payload = {"run_id":rid,"acquisition_id":aid,"action":tag,"effect":"measure_up",
               "mission_measure":"m","direction":"toward","mechanism":"candidate",
               "scope":{},"certainty":.8,"evidence":"http writer evidence"}
    assert client.post("/api/causal/governance/flagship/propose",json=payload).status_code == 403
    headers={"x-aq-governance-loop-token":os.environ["AQ_GOVERNANCE_LOOP_TOKEN"]}
    response=client.post("/api/causal/governance/flagship/propose",json=payload,headers=headers)
    assert response.status_code == 200, response.text
    edge_id=response.json()["edge_id"]
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO planning_prediction(edge_id,plan_id,work_id,step_id,step_index,source_action,expected_direct_effect,expected_direction,scope,edge_state,sufficient,predicted_certainty,resolved_run_id,executed,direct_effect_confirmed,direction_confirmed,outcome_kind,outcome_evidence) VALUES (%s,'p',%s,'s1',0,%s,'measure_up','toward','{}','solid',true,.8,%s,true,true,true,'confirmed','server stored evidence') RETURNING prediction_id",(edge_id,wid,tag,rid))
        pid=cur.fetchone()[0]
    response=client.post("/api/causal/governance/flagship/resolve",json={"edge_id":edge_id,"run_id":rid,"prediction_id":pid},headers=headers)
    assert response.status_code == 404
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT support_count FROM causal_edge WHERE edge_id=%s",(edge_id,))
        assert cur.fetchone()[0] == 0



def test_flagship_proposal_cannot_mutate_existing_identity():
    tag=_tag(); _,aid1,rid1=_acquisition_evidence(tag); _,aid2,rid2=_acquisition_evidence(tag)
    from fastapi.testclient import TestClient
    from management.api.app import app
    client=TestClient(app); headers={"x-aq-governance-loop-token":os.environ["AQ_GOVERNANCE_LOOP_TOKEN"]}
    def payload(aid,rid,evidence):
        return {"run_id":rid,"acquisition_id":aid,"action":tag,"effect":"measure_up",
                "mission_measure":"count","direction":"toward","mechanism":"m",
                "scope":{},"certainty":.8,"evidence":evidence}
    first=client.post("/api/causal/governance/flagship/propose",json=payload(aid1,rid1,"first"),headers=headers)
    assert first.status_code==200,first.text
    second=client.post("/api/causal/governance/flagship/propose",json=payload(aid2,rid2,"second"),headers=headers)
    assert second.status_code==409,second.text
    with psycopg2.connect(DSN) as conn,conn.cursor() as cur:
        cur.execute("SELECT evidence_run_ids FROM causal_edge WHERE source_action=%s AND direct_effect='measure_up'",(tag,))
        assert cur.fetchone()[0]==[rid1]

def test_reflection_recovery_preserves_structured_acquisition_proposal_for_writer_retry():
    tag=_tag(); plan={"steps":[{"step_id":"s1","action":tag,"expected_effect":"measure_up","expected_direction":"toward","scope":[]}]}
    # Runtime plans use object scope; proposal schema uses key/value records.
    plan["steps"][0]["scope"]={}
    proposal={"source_action":tag,"direct_effect":"measure_up","mission_measure":"m",
              "direction":"toward","mechanism":"candidate","scope":[],"certainty":.8,
              "evidence":"structured candidate receipt"}
    act={"outcome":"acquired","succeeded":True,"evidence":"durable ACT receipt",
         "observed_metrics":{},"step_results":[],"causal_proposals":[proposal]}
    with psycopg2.connect(DSN) as conn,conn.cursor() as cur:
        cur.execute("INSERT INTO work(kind,summary,rationale,status,plan_id,plan) VALUES (%s,'recover','recover','running','p',%s::jsonb) RETURNING id",(tag,__import__('json').dumps(plan)))
        wid=cur.fetchone()[0]
        cur.execute("INSERT INTO plan_acquisition(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction,status) VALUES (%s,'p','s1','search',1,'a1','search','running') RETURNING acquisition_id",(wid,))
        aid=cur.fetchone()[0]
        cur.execute("INSERT INTO runs(work_id,acquisition_id,outcome,succeeded,evidence,measure_before,act_result) VALUES (%s,%s,'acquired',true,'durable ACT receipt',0,%s::jsonb) RETURNING id",(wid,aid,__import__('json').dumps(act)))
        rid=cur.fetchone()[0]
    class ReflectOnly:
        def run(self,prompt,schema,tier="working", **kwargs):
            from runner.executor import Usage
            return {"insight":"recovered structured receipt","evidence":"run receipt",
                    "scope":"local","confidence":.5},Usage()
    from runner.config import Instance
    from runner.loop import Loop
    db=Db(LOOP_DSN,graph="none",connect_retries=1)
    instance_path = "/app/instance.yaml" if os.path.exists("/app/instance.yaml") else "templates/running-a-business/instance.yaml"
    Loop(Instance.load(instance_path),db,ReflectOnly()).finish_pending_reflection()
    pending=db.pending_flagship_proposals()
    assert len(pending)==1 and pending[0]["run_id"]==rid and pending[0]["acquisition_id"]==aid
    assert pending[0]["evidence"]=="structured candidate receipt"
    with psycopg2.connect(DSN) as conn,conn.cursor() as cur:
        cur.execute("SELECT completed_at IS NOT NULL,act_result->'causal_proposals' IS NOT NULL FROM runs WHERE id=%s",(rid,))
        assert cur.fetchone()==(True,True)
        cur.execute("SELECT status,result->>'recovered_reflection' FROM plan_acquisition WHERE acquisition_id=%s",(aid,))
        assert cur.fetchone()==("completed","true")


def test_governance_writer_re_reads_real_mission_measure_with_its_own_principal():
    from management.api.app import _independent_mission_measure
    value,reached=_independent_mission_measure()
    with psycopg2.connect(GOVERNANCE_DSN) as conn,conn.cursor() as cur:
        cur.execute("SELECT count(distinct customer_id) FROM subscriptions WHERE status='active'")
        expected=float(cur.fetchone()[0])
    assert value==expected and reached is False
