#!/usr/bin/env python3
"""Deterministic acceptance proof for task #4834 self-correction slice."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner.consultants.seam import ConsultantPass, Recommendation
from runner.consultants.self_correction import (
    AuditValue, Correction, PrincipleHypothesis, ReferenceEvent, ReferenceKind,
    SelfCorrectionSnapshot, audit_voi_decision, classify_correction, consult,
)


def p(pid: str, validated: str, *, rule: str = "rule-list-classifier") -> PrincipleHypothesis:
    return PrincipleHypothesis(pid, rule, "safe", validated, f"fixture:{pid}")


def snapshot(*, gap: bool) -> SelfCorrectionSnapshot:
    return SelfCorrectionSnapshot(
        Correction("one", "rule-list-classifier", "safe", "unsafe"),
        (
            p("one", "unsafe"),
            p("two", "unsafe" if gap else "safe"),
            p("three", "unsafe" if gap else "safe"),
            p("non-sibling", "unsafe", rule="another-rule"),
        ),
        AuditValue(Decimal("1"), Decimal("2"), Decimal("1"), "fixture:voi"),
    )


def main() -> None:
    gap_result = consult(snapshot(gap=True))
    fine_result = consult(snapshot(gap=False))
    learned = classify_correction(snapshot(gap=True).correction)
    assert isinstance(gap_result, Recommendation)
    assert "suspect siblings [two, three]" in gap_result.rationale
    assert "non-sibling" not in gap_result.rationale
    assert isinstance(fine_result, ConsultantPass)
    assert "all siblings validated, no change" in fine_result.rationale
    assert learned is not None
    new_match = learned.fires_on(
        ReferenceEvent(ReferenceKind.COLLECTION_MEMBER, True)
    )
    non_match = learned.fires_on(
        ReferenceEvent(ReferenceKind.STANDALONE_ITEM, False)
    )
    assert new_match and not non_match
    voi = audit_voi_decision(snapshot(gap=True).audit_value)
    packet = {
        "real_gap": {
            "outcome": "recommendation",
            "action": gap_result.action.value,
            "rationale": gap_result.rationale,
        },
        "fine_siblings": {
            "outcome": "PASS",
            "rationale": fine_result.rationale,
        },
        "meta_learning": {
            "type": learned.learning_type_id,
            "response": learned.response.value,
            "fires_on_new_match": new_match,
            "fires_on_non_match": non_match,
        },
        "voi_governor": {
            "policy_version": voi.policy_version,
            "decision": voi.decision,
            "chosen_mode": voi.chosen_mode.value if voi.chosen_mode else None,
        },
        "shared_seam": {
            "recommendation_fields": list(asdict(gap_result)),
            "source": gap_result.source,
        },
    }
    print(json.dumps(packet, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
