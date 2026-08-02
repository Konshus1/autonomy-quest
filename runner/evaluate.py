"""Stage 7 — Evaluate the worker's work (EVAL_MERGE_SPEC.md, first slice).

Folds into the loop's productivity computation: `evaluate()` returns `was_productive`'s boolean
UNCHANGED (so the escalation ladder, hibernation floor, and verify.sh's run<->learning gate are
all untouched) plus a richer verdict that drives the manager-gated merge step. Pure — no DB, no
network — so it is safe on the critical path.

Verdict logic (first slice; hrf-2878's Gate 3/4 modules swap into gate3_/gate4_ later):
  rework   — the work isn't done (tests/artifact failed, intent not covered, or the learning is
             not complete). The loop records productive=false and climbs the EXISTING ladder.
  escalate — the code did its job but the causal model is incoherent with reality (a close call
             a human should see) -> emits escalate_human (with an uncertainty_note).
  pass     — clean -> emits approve_merge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_KNOWN_SCOPES = {"local", "generalisable"}


@dataclass
class EvalResult:
    productive: bool          # returned to the loop UNCHANGED — the ladder's input (== was_productive)
    test_level: str           # 'ui' | 'api' | 'unit' | 'none'
    tests_passed: bool
    intent_covered: bool
    completeness_ok: bool     # Gate 3
    coherence_ok: bool        # Gate 4
    verdict: str              # 'pass' | 'rework' | 'escalate'
    detail: str


def classify_test_level(work: Any, evidence: str) -> str:
    """Highest feasible signal the evidence demonstrates. Stub heuristic (recorded, not enforced)."""
    e = (evidence or "").lower()
    if "playwright" in e or "e2e" in e or "screenshot" in e:
        return "ui"
    if "curl" in e or "endpoint" in e or "/api/" in e:
        return "api"
    if "pytest" in e or "assert" in e or " test" in e:
        return "unit"
    return "none"


def check_intent_coverage(work: Any, evidence: str) -> bool:
    """STUB (honest partial): not yet a hard gate — always covered until a classifier lands.
    Flagged in the eval detail so 'intent-coverage enforced' is never over-claimed."""
    return True


def gate3_learning_completeness(insight: Any) -> bool:
    """Gate 3 — the cycle produced a non-trivial, checkable learning (a learning row exists with
    pointable evidence and a known scope). Same 'is there pointable evidence' spirit as
    was_productive, applied to the LEARNING rather than the act. Reads the freshly-built insight
    dict (no committed-row read -> no read-before-write hazard)."""
    if not isinstance(insight, dict):
        return False
    ev = str(insight.get("evidence") or "").strip()
    scope = str(insight.get("scope") or "")
    return bool(ev) and "n/a" not in ev.lower() and scope in _KNOWN_SCOPES


def gate4_model_coherence(predicted_certainty: float | None, actual_success: bool) -> bool:
    """Gate 4 — the outcome does not CONTRADICT a confident causal prediction. High surprise on a
    high-certainty edge = the world disagreed with the model = a close call the manager shouldn't
    auto-approve. Reads the surprise; never writes support or promotes an edge. No governing
    principle (None) -> nothing to be incoherent with -> coherent."""
    if predicted_certainty is None:
        return True
    if predicted_certainty >= 0.7 and not actual_success:
        return False
    return True


class Evaluator:
    def __init__(self, esc) -> None:
        self.esc = esc

    def evaluate(self, *, work, outcome: str, succeeded: bool, evidence: str,
                 measure_before, measure_after, insight, predicted_certainty) -> EvalResult:
        tests_passed = self.esc.was_productive(  # REUSE — do not reimplement the productivity truth
            outcome, succeeded, evidence, measure_before, measure_after)
        test_level = classify_test_level(work, evidence)
        intent_ok = check_intent_coverage(work, evidence)
        completeness = gate3_learning_completeness(insight)
        actual_success = measure_after > measure_before
        coherence = gate4_model_coherence(predicted_certainty, actual_success)

        if not tests_passed or not intent_ok or not completeness:
            verdict = "rework"
        elif not coherence:
            verdict = "escalate"      # coherent-but-surprising -> a human should look
        else:
            verdict = "pass"

        detail = (f"tests={tests_passed} level={test_level} intent={intent_ok} "
                  f"complete={completeness} coherent={coherence} (intent-coverage is a stub, not enforced)")
        return EvalResult(
            productive=tests_passed, test_level=test_level, tests_passed=tests_passed,
            intent_covered=intent_ok, completeness_ok=completeness, coherence_ok=coherence,
            verdict=verdict, detail=detail)
