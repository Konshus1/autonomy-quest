from unittest.mock import patch
from runner import causal_sync, prompts
from runner.loop import Loop
from tests.test_consult_act_wiring import ParkDb, DecideExecutor, inst_external

INSTANCE="urn:uuid:11111111-1111-4111-8111-111111111111"

class GovernedDb(ParkDb):
    def __init__(self):
        super().__init__(); self.governance_blocks=[]; self.governance_defers=[]; self.authorization_ids=[]
    def bind_governance_plan(self,work_id,global_plan_id,plan): self.bound_plan=(work_id,global_plan_id,plan)
    def reject_work_governance(self,work_id,reason): self.governance_blocks.append((work_id,reason))
    def defer_plan_governance(self,work_id,global_plan_id,request_digest,reason_code,detail):
        self.governance_defers.append((work_id,global_plan_id,request_digest,reason_code,detail))
    def start_run(self,work_id,plan_authorization_id=None):
        self.authorization_ids.append(plan_authorization_id)
        return super().start_run(work_id)


def decision(disposition):
    matrix={"allow":(True,True,True),"block":(False,False,False),"abstain":(True,False,False),"defer":(False,False,False)}
    may,selected,governed=matrix[disposition]
    return causal_sync.PlanAuthorizationDecision(
      disposition=disposition,reason_code={"allow":"all_steps_exactly_governed","block":"promoted_direction_conflict","abstain":"no_applicable_promoted_governor","defer":"governance_unreachable"}[disposition],
      reason=f"reason:{disposition}",may_act=may,selected=selected,governed=governed,
      global_plan_id=f"{INSTANCE}/plan/22222222-2222-4222-8222-222222222222",request_digest="a"*64,
      authorization_id=None if disposition=="defer" else "9cf4ed5f-75ca-47d6-a798-13b06f5c8b27",
      governor_transition_ids=(7,) if disposition in {"allow","block"} else ())


def run_governed(disposition):
    db=GovernedDb();ex=DecideExecutor()
    env={"AQ_GOVERNED_FEEDBACK":"1","AQ_GOVERNANCE_URL":"http://narrow","AQ_GOVERNANCE_DECISION_TOKEN":"secret","AQ_INSTANCE_ID":INSTANCE}
    with patch.dict("os.environ",env,clear=False), patch("runner.causal_sync.authorize_plan",return_value=decision(disposition)) as authorize:
        cycle=Loop(inst_external(),db,ex).cycle()
    return cycle,db,(prompts.ACT_SCHEMA in ex.schemas),authorize


def test_governed_block_stops_before_expense_run_and_act():
    cycle,db,acted,authorize=run_governed("block")
    assert cycle is None and not acted and db.started==[] and not hasattr(db,"spend_reservations")
    assert db.governance_blocks==[(99,"reason:block")] and authorize.call_count==1
    assert authorize.call_args.args[2]["steps"][0]["expected_direction"]=="toward"


def test_governed_defer_parks_before_expense_run_and_act():
    cycle,db,acted,_=run_governed("defer")
    assert cycle is None and not acted and db.started==[] and not hasattr(db,"spend_reservations")
    assert db.governance_defers[0][3]=="governance_unreachable"


def test_governed_allow_receipt_precedes_exactly_one_run_and_act():
    cycle,db,acted,_=run_governed("allow")
    assert cycle is not None and acted and db.started==[99]
    assert db.authorization_ids==["9cf4ed5f-75ca-47d6-a798-13b06f5c8b27"]


def test_authenticated_abstain_is_honest_but_base_policy_may_act():
    cycle,db,acted,_=run_governed("abstain")
    assert cycle is not None and acted and db.started==[99] and db.governance_blocks==[]
    assert db.authorization_ids==["9cf4ed5f-75ca-47d6-a798-13b06f5c8b27"]
