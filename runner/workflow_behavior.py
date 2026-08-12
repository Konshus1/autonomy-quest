"""Workflow-behavior seam (Step 3, Slice 0 — PROVABLY INERT).

This module introduces the seam that a later slice will use to let a workflow
plugin *interpret* how each stage of the loop turns its inputs into the
`(prompt, schema, tier)` triple the executor consumes. It changes ZERO loop
behavior: the loop constructs a behavior object but does not yet call it (the
`prompts.*` call sites in `runner/loop.py` are unflipped in Slice 0).

The keystone invariant of this slice is byte-identity: `DefaultBehavior`
reproduces today's values VERBATIM, so wiring it in later cannot change what the
loop emits. See `tests/test_workflow_behavior.py` for the golden proof.

Slice 1 (later) flips the call sites in `runner/loop.py` to route through
`self.behavior`, and replaces the non-default branch of `resolve_behavior` with
a real `YamlBehavior` that interprets a workflow plugin. Neither exists here.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from . import prompts


# The triple every stage produces and the executor already consumes:
#   (prompt: str, schema: dict, tier: str)
Triple = tuple[str, dict, str]


@runtime_checkable
class StageBehavior(Protocol):
    """How a workflow turns each stage's inputs into the executor triple.

    PURE by contract: implementations must not touch the DB, run gates, or cause
    any side effect. They only shape inputs the loop already holds into the
    `(prompt, schema, tier)` triple. All gating, persistence, and control flow
    stay in the loop; this seam decides only what to say and how to shape it.
    """

    def decide(self, world: dict, template: str, guidance: str) -> Triple:
        """DECIDE stage: choose the single highest-value thing to do now."""
        ...

    def act(self, work: Any, boundaries: Any) -> Triple:
        """ACT stage: do the chosen work within the mission boundaries."""
        ...

    def reflect(self, work: Any, outcome: str, succeeded: bool, prior: Any) -> Triple:
        """REFLECT stage: learn from what actually happened."""
        ...


class DefaultBehavior:
    """The stock loop behavior, reproducing today's values byte-identically.

    Each method returns EXACTLY what the corresponding `runner/loop.py` call site
    produces today (same `prompts.*` call, same schema object, same tier string),
    so routing the loop through this behavior in a later slice is a no-op.
    """

    def decide(self, world: dict, template: str, guidance: str) -> Triple:
        # Mirrors loop.py ~384:
        #   prompts.decide(world, self.inst.template, guidance=verdict.guidance),
        #   prompts.DECIDE_SCHEMA, tier="reasoning"
        return (
            prompts.decide(world, template, guidance=guidance),
            prompts.DECIDE_SCHEMA,
            "reasoning",
        )

    def act(self, work: Any, boundaries: Any) -> Triple:
        # Mirrors loop.py ~628:
        #   prompts.act(work, self.inst.mission.boundaries), prompts.ACT_SCHEMA, tier="working"
        return (
            prompts.act(work, boundaries),
            prompts.ACT_SCHEMA,
            "working",
        )

    def reflect(self, work: Any, outcome: str, succeeded: bool, prior: Any) -> Triple:
        # Mirrors loop.py ~666 / ~1332:
        #   prompts.reflect(work, outcome, succeeded, self.db.live_learnings(limit=50)),
        #   prompts.REFLECT_SCHEMA, tier="reasoning"
        return (
            prompts.reflect(work, outcome, succeeded, prior),
            prompts.REFLECT_SCHEMA,
            "reasoning",
        )


def resolve_behavior(workflow: Any) -> StageBehavior:
    """Pick the StageBehavior for a workflow.

    Slice 0: ALWAYS returns `DefaultBehavior()`. The default identity
    ("default/v1") maps to it explicitly; every other identity ALSO maps to it
    FOR NOW, because there is no interpreter yet — Slice 0 introduces only the
    inert seam.

    TODO(Slice 1): replace the non-default branch below with a `YamlBehavior`
    that interprets the workflow plugin. Until then a non-default identity is
    indistinguishable from default at runtime, by design — landing Slice 0 must
    change nothing the loop does.
    """
    if workflow.identity == "default/v1":
        return DefaultBehavior()
    # Slice 0: no YamlBehavior yet — fall back to the stock behavior so the seam
    # stays inert for non-default workflows too. Slice 1 replaces this branch.
    return DefaultBehavior()
