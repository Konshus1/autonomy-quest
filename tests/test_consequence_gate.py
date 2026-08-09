from decimal import Decimal
import pytest
from runner.consequence_gate import assess_blast_radius,assess_plan_gate,expense

def facts(n=0,public=False,production=False,irreversible=False):
 return {"affected_entities_upper_bound":n,"public_or_unbounded":public,
         "production_wide":production,"irreversible_external_write":irreversible}
def plan(cost,blast,**flags):
 return {"expected_expense_usd":cost,"steps":[{"blast_radius":blast}],**flags}

def test_strictly_over_three_dollars_requires_approval():
 assert assess_plan_gate(plan(3,facts())).requires_human is False
 assert assess_plan_gate(plan("3.01",facts())).requires_human is True

def test_obviously_dangerous_mass_send_gates_at_zero_cost():
 d=assess_plan_gate(plan(0,facts(100)))
 assert d.requires_human is True and d.blast_radius_level==3

def test_irreversible_external_write_can_return_high():
 assert assess_blast_radius(facts(1,irreversible=True)).level==3

def test_categories_alone_do_not_gate_low_consequence_action():
 d=assess_plan_gate(plan(0,facts(1),touches_human=True,commits=True,spends_money=True))
 assert d.requires_human is False

def test_unknown_or_malformed_blast_fails_high():
 assert assess_plan_gate(plan(0,{})).requires_human is True
 assert assess_plan_gate(plan(0,facts(-1))).blast_radius_level==3

def test_money_is_decimal_and_rejects_malformed():
 assert expense("0.10")==Decimal("0.10")
 for value in (-1,float("nan"),float("inf"),True,None):
  with pytest.raises(ValueError): expense(value)
