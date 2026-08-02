"""Consult-ACT (BB #746, Slice 3) — the first step where a mined causal principle's
certainty actually INFLUENCES the decision, not merely scores it.

Until now the loop CONSULTS principles only to PREDICT: it reads the governing edge's
certainty before acting and checks that prediction against reality (the surprise loop).
That consult is read-only and, by hard rule, can never stop an act. Consult-act adds a
*separate, distinct* use of the same certainty that is allowed to change what the loop
does — but in exactly ONE direction.

THE ONE-DIRECTIONAL INVARIANT (this is what makes it safe to ship):
  Consult-act may only make the loop MORE cautious. Concretely, a LOW-certainty,
  side-effecting step is DEFERRED to the human-review path that already exists (park +
  notify) instead of being executed autonomously. Consult-act can NEVER:
    - authorize a step that the base autonomy gate wouldn't already allow,
    - un-park or un-gate work,
    - make any gate more permissive.
  It is wired as an OR into the existing `requires_human` gate: it can add a reason to
  defer, never remove one. So the worst a wrong or missing principle can do is ask a
  human — never act recklessly. That property is the whole safety argument.

WHAT IT IS NOT (still roadmap): consult-act does not yet REORDER or SELECT among
candidate plans, and it does not apply promotions. It only gates a single decided step.
"""
from __future__ import annotations

# Below this governing-edge certainty, a side-effecting step is deferred to review.
# Deliberately low: consult-act should defer only when a principle is genuinely
# pessimistic about the step, not merely un-confident.
CONSULT_ACT_GATE_THRESHOLD = 0.35


def is_side_effecting(work) -> bool:
    """Mirror the side-effect signals the `act-reversible` autonomy level already uses,
    so consult-act's notion of 'risky enough to defer on low certainty' matches the
    rest of the gate rather than inventing a second definition."""
    return (
        not work.reversible
        or work.spends_money
        or work.touches_human
        or work.commits
    )


def should_defer(work, predicted_certainty, threshold: float = CONSULT_ACT_GATE_THRESHOLD) -> bool:
    """Return True iff consult-act wants this decided step deferred to human review.

    Deferral requires ALL of:
      - a governing principle actually exists (predicted_certainty is not None). Absence
        of a principle is NOT caution-worthy here — the base gate already handles genuine
        unknowns; consult-act acts only on POSITIVE low-certainty evidence.
      - the step is side-effecting (reversible no-side-effect work is not worth deferring
        on a low certainty — the base gate already lets it run).
      - the governing certainty is below `threshold`.
    """
    if predicted_certainty is None:
        return False
    if not is_side_effecting(work):
        return False
    return predicted_certainty < threshold


def gate_reason(work, predicted_certainty, threshold: float = CONSULT_ACT_GATE_THRESHOLD) -> str | None:
    """A short, auditable reason string when consult-act defers, else None. This is what a
    human sees on the parked item so the deferral is never silent."""
    if not should_defer(work, predicted_certainty, threshold):
        return None
    return (
        f"consult-act: the governing causal principle gives this side-effecting step a "
        f"low certainty ({predicted_certainty:.2f} < {threshold:.2f}) — deferred to review "
        f"rather than acted autonomously."
    )
