#!/usr/bin/env python3
"""Exact built-runtime proof that checked block/allow decisions precede run and ACT."""
from __future__ import annotations
import json,os,uuid
from decimal import Decimal
import psycopg2
from runner.config import Instance
from runner.db import Db,Work
from runner.executor import Usage
from runner.intent_lineage import mission_intent_contract
from runner.loop import Loop
from runner import prompts

class SeamExecutor:
    def __init__(self): self.act_calls=0
    def run(self,prompt,schema,tier="working"):
        if schema is prompts.ACT_SCHEMA:
            self.act_calls+=1
            return ({"outcome":"exact M4 seam act","succeeded":True,"evidence":"exact:m4",
                     "observed_metrics":[{"metric":"mission_delta","value":0},{"metric":"mission_value","value":0}],
                     "step_results":[{"step_id":"exact","executed":True,"confirmed":True,
                                      "harmed_concern_ids":[],"evidence":"exact:m4"}]},Usage())
        if schema is prompts.REFLECT_SCHEMA:
            return ({"insight":"checked authority preceded this run","evidence":"exact:m4",
                     "scope":"local","confidence":.9},Usage())
        raise AssertionError(f"unexpected model seam {schema}")

def global_id(): return f"{os.environ['AQ_INSTANCE_ID']}/plan/{uuid.uuid4()}"

def plan_for(inst,action,effect,scope,direction):
    concerns=mission_intent_contract(inst.mission,inst.mission.measure.target)
    return {"goal_predicate":concerns[0]["predicate"],"expected_expense_usd":0,
      "mission_concerns":concerns,
      "subgoals":[{"subgoal_id":"exact-subgoal","success_predicate":concerns[-1]["predicate"],
                   "serves_concern_ids":[c["concern_id"] for c in concerns]}],
      "steps":[{"step_id":"exact","subgoal_id":"exact-subgoal","action":action,
                "expected_effect":effect,"expected_direction":direction,"scope":scope,
                "blast_radius":{"affected_entities_upper_bound":0,"public_or_unbounded":False,
                  "production_wide":False,"irreversible_external_write":False}}]}

def create(db,plan,plan_id):
    wid=db.create_work("exact-m4","exact M4 plan","prove checked authority",False,
      plan_id=plan_id,plan=plan,expected_expense_usd=Decimal("0"),blast_radius_level=0,
      blast_radius_basis={},gate_policy_version="exact",gate_reason="within base gates",
      reversible=True,spends_money=False,expected_cost_usd=Decimal("0"),blast_radius=0,
      touches_human=False,commits=False)
    return Work(id=wid,kind="exact-m4",summary="exact M4 plan",rationale="prove checked authority",
      plan_id=plan_id,plan=plan,expected_expense_usd=Decimal("0"),blast_radius_level=0,
      blast_radius_basis={},gate_policy_version="exact",gate_reason="within base gates")

inst=Instance.load("/app/instance.yaml")
db=Db(os.environ["AQ_DB_URL"],graph="none")
with db.conn.cursor() as q:
 q.execute("""select e.source_action,e.direct_effect,e.scope_conditions,e.relation_direction
 from causal_edge e where (select t.to_status from causal_principle_transition t
  where t.cause=e.source_action and t.effect=e.direct_effect and t.scope=e.scope_conditions
  order by t.id desc limit 1)='promoted' order by e.edge_id limit 1""")
 edge=q.fetchone()
assert edge is not None
cause,effect,scope_text,direction=edge; scope=json.loads(scope_text)
ex=SeamExecutor();loop=Loop(inst,db,ex)

# Content-specific contradiction: hide the corroborative local relation so only the independently
# checked remote authority can stop it, and mark it as an already-routed acquisition to avoid an
# unrelated frame-gap controller branch.
opp={"toward":"away","away":"toward","neutral":"toward"}[direction]
block_plan=plan_for(inst,cause,effect,scope,opp); block_id=global_id(); block_work=create(db,block_plan,block_id)
block_work.acquisition_id=9223372036854775000
original_relations=db.known_plan_relations; db.known_plan_relations=lambda: []
before=db._q("select count(*) n from runs where work_id=%s",(block_work.id,),one=True)["n"]
assert loop.execute_work(block_work,measure_before=db.read_measure(inst.mission.measure),decision_usage=Usage()) is None
after=db._q("select count(*) n from runs where work_id=%s",(block_work.id,),one=True)["n"]
assert before==after==0 and ex.act_calls==0
with psycopg2.connect(os.environ["AQ_C4_CONTROL_DSN"]) as control, control.cursor() as q:
 q.execute("select disposition,selected,governed from aq_control.plan_authorization where global_plan_id=%s",(block_id,))
 receipt=q.fetchone()
assert receipt==("block",False,False)
assert db._q("select status from work where id=%s",(block_work.id,),one=True)["status"]=="abandoned"

# Matching promoted direction: the durable receipt exists first, its ID is carried by the one run,
# and the external model seam is called exactly once. A reject-everything implementation fails.
db.known_plan_relations=original_relations
allow_plan=plan_for(inst,cause,effect,scope,direction); allow_id=global_id(); allow_work=create(db,allow_plan,allow_id)
cycle=loop.execute_work(allow_work,measure_before=db.read_measure(inst.mission.measure),decision_usage=Usage())
assert cycle is not None and ex.act_calls==1
with psycopg2.connect(os.environ["AQ_C4_CONTROL_DSN"]) as control, control.cursor() as q:
 q.execute("""select r.plan_authorization_id,a.disposition,a.selected,a.governed
 from runs r join aq_control.plan_authorization a on a.authorization_id=r.plan_authorization_id
 where r.work_id=%s""",(allow_work.id,))
 row=q.fetchone()
assert row[0] and row[1:]==("allow",True,True)
assert len(db._q("select id from runs where work_id=%s",(allow_work.id,)))==1
print("M4 PRE-ACT OK: promoted contradiction made zero runs/ACT; exact allow receipt preceded one run/ACT")
