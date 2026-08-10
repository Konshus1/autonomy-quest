"""C4 PostgreSQL controls: actor proposals never grant flagship planning authority."""
from __future__ import annotations
import json
import os
import uuid
from types import SimpleNamespace

import psycopg2
import pytest

from management.api.principle_governance import PgGovernedPrincipleLifecycle, PromotionRefused
from runner.db import Db, Work

DSN = os.environ.get("AQ_C4_TEST_DSN")
ACTOR_DSN = os.environ.get("AQ_C4_ACTOR_DSN")
LOOP_DSN = os.environ.get("AQ_C4_LOOP_DSN")
GOVERNANCE_DSN = os.environ.get("AQ_C4_GOVERNANCE_DSN")
_REQUIRED_DSNS = (DSN, ACTOR_DSN, LOOP_DSN, GOVERNANCE_DSN)
pytestmark = pytest.mark.skipif(
    not all(_REQUIRED_DSNS), reason="set all four AQ_C4 principal DSNs for C4 controls"
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


def test_acquisition_stages_exact_candidate_with_run_in_one_transaction():
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
    proposal = {"source_action": tag, "direct_effect": "measure_up", "mission_measure": "m", "direction": "toward", "mechanism": "candidate only", "scope": [], "certainty": .95, "evidence": "durable run receipt"}
    with db.tx() as cur:
        cur.execute("UPDATE runs SET completed_at=now(),outcome='acquired',succeeded=true WHERE id=%s", (rid,))
        edge_ids = db.stage_acquired_relations(cur, rid, work, [proposal])
    assert len(edge_ids) == 1
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT evidence_run_ids FROM causal_edge WHERE edge_id=%s", (edge_ids[0],))
        assert cur.fetchone()[0] == [rid]
        cur.execute("SELECT to_status,authority_after,evidence_ref FROM causal_principle_transition WHERE cause=%s", (tag,))
        status, authority, evidence_ref = cur.fetchone()
        assert (status, authority) == ("provisional", False)
        assert evidence_ref.startswith(f"run:{rid}/")
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


def test_loop_cannot_write_exact_trusted_evidence_table():
    with psycopg2.connect(LOOP_DSN) as conn, conn.cursor() as cur:
        with pytest.raises(
            psycopg2.errors.InsufficientPrivilege,
            match="permission denied for table evidence_event",
        ):
            cur.execute(
                "INSERT INTO aq_control.evidence_event("
                "event_id,event_kind,subject_key,run_id,payload,payload_digest) "
                "VALUES (%s,'acquisition_completed','forged',1,'{}','forged')",
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
