"""Deterministic consequence gate from amended BB #878.

Authorization depends on numeric per-plan expense and measured per-action blast radius,
not categories such as "touches a human", "commits", or boolean "spends money".
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

POLICY_VERSION = "bb878-amended-v2"

@dataclass(frozen=True)
class BlastAssessment:
    level: int
    reason: str

@dataclass(frozen=True)
class GateDecision:
    requires_human: bool
    reason: str | None
    expected_expense_usd: Decimal
    blast_radius_level: int
    policy_version: str = POLICY_VERSION


def expense(value) -> Decimal:
    if isinstance(value, bool): raise ValueError("expected expense must be numeric")
    try: amount=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError) as exc: raise ValueError("expected expense must be finite numeric") from exc
    if not amount.is_finite() or amount < 0: raise ValueError("expected expense must be finite and nonnegative")
    return amount


def assess_blast_radius(facts: Mapping[str,object]) -> BlastAssessment:
    required={"affected_entities_upper_bound","public_or_unbounded","production_wide","irreversible_external_write"}
    if not isinstance(facts,Mapping) or set(facts)!=required:
        return BlastAssessment(3,"missing or unknown blast-radius facts")
    count=facts["affected_entities_upper_bound"]
    if isinstance(count,bool) or not isinstance(count,int) or count<0:
        return BlastAssessment(3,"invalid affected-entity bound")
    if facts["public_or_unbounded"] is True: return BlastAssessment(3,"public or unbounded consequence")
    if facts["production_wide"] is True: return BlastAssessment(3,"production-wide consequence")
    if facts["irreversible_external_write"] is True: return BlastAssessment(3,"irreversible external write")
    if any(not isinstance(facts[key],bool) for key in required-{"affected_entities_upper_bound"}):
        return BlastAssessment(3,"invalid blast-radius boolean")
    if count>10: return BlastAssessment(3,f"bounded consequence affects {count} entities (>10)")
    if count>1: return BlastAssessment(2,f"bounded consequence affects {count} entities")
    if count==1: return BlastAssessment(1,"single-entity consequence")
    return BlastAssessment(0,"local/no external entity consequence")


def assess_plan_gate(plan: Mapping[str,object], *, per_plan_approval_usd=Decimal("3"),
                     blast_gate_level:int=3) -> GateDecision:
    amount=expense(plan.get("expected_expense_usd"))
    steps=plan.get("steps") or []
    blasts=[assess_blast_radius(step.get("blast_radius")) for step in steps]
    max_blast=max((b.level for b in blasts),default=3)
    reasons=[]
    threshold=expense(per_plan_approval_usd)
    if amount>threshold: reasons.append(f"expected plan expense ${amount} exceeds ${threshold}")
    if max_blast>=blast_gate_level:
        reason=next((b.reason for b in blasts if b.level==max_blast),"unknown blast radius")
        reasons.append(f"blast radius level {max_blast}: {reason}")
    return GateDecision(bool(reasons),"; ".join(reasons) or None,amount,max_blast)
