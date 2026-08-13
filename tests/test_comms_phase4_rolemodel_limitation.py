"""KNOWN-LIMITATION LOCK (#4834 comms Phase 4): per-role ``model``/``context`` are NOT applied
per-call on the subscription execution path.

Phase 4 role agents DECLARE a ``model`` and a ``context``, but ``SubscriptionRoleWorker.run_turn``
drives the SAME base executor for every role and passes NEITHER ``agent.model`` NOR ``agent.context``
into ``base_executor.run``. Only ``prompt_template`` shapes the turn. ``model`` is consulted solely by
the reviewer-independence check; ``context`` solely by that check's same-context comparison.

These are CHARACTERIZATION tests: they assert the CURRENT reality (documented in ``COMMS_PHASE4.md``
and the ``SubscriptionRoleWorker`` / ``RoleAgent`` docstrings), so that if someone later wires a real
per-call model override WITHOUT updating the docs, they fail LOUDLY — prompting the docs fix too. They
do NOT endorse the limitation as desirable; heterogeneous per-role models are a future enhancement.
"""

from __future__ import annotations

from runner.executor import Usage
from runner.role_config import RoleAgent, RoleConfig
from runner.comms_runtime.multi_agent_executor import (
    MultiAgentExecutor,
    SubscriptionRoleWorker,
    _default_worker_factory,
)
from runner.comms_runtime.wake_delivery import SubprocessWakeDelivery


class _RecordingExecutor:
    """A stand-in base executor that records every ``run`` call verbatim.

    It is a SINGLE object; the point of the lock is that every role drives THIS SAME object — there
    is no per-role engine selection on the subscription path.
    """

    def __init__(self):
        self.calls: list[dict] = []

    def run(self, prompt, schema, tier="working"):
        self.calls.append({"prompt": prompt, "schema": schema, "tier": tier, "self": id(self)})
        # Echo a schema-shaped reply; content is irrelevant to this lock.
        return ({"kind": "research", "plan": {"steps": []}} if schema and "plan" in
                schema.get("properties", {}) else {"verdict": "ok"}), Usage()


_DISTINCT_MODEL = "claude-superduper-42-per-role-ONLY-declared-never-executed"
_DISTINCT_CTX_LABEL = "SECRET-CONTEXT-LABEL-that-must-not-reach-the-prompt"


def _decide_schema():
    return {
        "type": "object",
        "required": ["kind", "plan"],
        "properties": {"kind": {"type": "string"}, "plan": {"type": "object"}},
    }


def test_producer_turn_does_not_pass_declared_model_or_context_to_base_executor():
    """The producer role declares a distinct model+context; NEITHER reaches ``base_executor.run``.

    Only ``prompt_template`` shapes the prompt. This is the crux of the limitation: a role's declared
    ``model`` is not a per-call model switch, and ``context`` is not injected into the prompt.
    """
    rec = _RecordingExecutor()
    agent = RoleAgent(
        role="coder",
        model=_DISTINCT_MODEL,
        prompt_template="CODER-TEMPLATE-TEXT",
        context=(_DISTINCT_CTX_LABEL,),
    )
    worker = SubscriptionRoleWorker(agent=agent, base_executor=rec, base_prompt="BASE-PROMPT")

    payload, _ = worker.run_turn(role="coder", inbox=[], schema=_decide_schema(), tier="reasoning")

    assert payload == {"decision": {"kind": "research", "plan": {"steps": []}}}
    assert len(rec.calls) == 1
    call = rec.calls[0]

    # tier IS forwarded (the one per-call knob that is honored).
    assert call["tier"] == "reasoning"
    # prompt_template IS applied.
    assert "CODER-TEMPLATE-TEXT" in call["prompt"]
    # The declared model is NOT applied: it appears in no argument of the run call (not the prompt,
    # not the schema, not the tier). Nothing routes the turn to that model.
    assert _DISTINCT_MODEL not in call["prompt"]
    assert _DISTINCT_MODEL not in str(call["schema"])
    assert _DISTINCT_MODEL != call["tier"]
    # The declared context is NOT injected into the prompt.
    assert _DISTINCT_CTX_LABEL not in call["prompt"]


def test_reviewer_turn_also_ignores_declared_model_and_context():
    """A reviewer turn (schema=None) likewise passes neither model nor context to the base executor."""
    rec = _RecordingExecutor()
    agent = RoleAgent(
        role="reviewer",
        model=_DISTINCT_MODEL,
        prompt_template="REVIEWER-TEMPLATE-TEXT",
        context=(_DISTINCT_CTX_LABEL,),
        independent=True,
    )
    worker = SubscriptionRoleWorker(agent=agent, base_executor=rec, base_prompt="BASE-PROMPT")

    payload, _ = worker.run_turn(role="reviewer", inbox=[], schema=None, tier="working")

    assert "verdict" in payload
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert "REVIEWER-TEMPLATE-TEXT" in call["prompt"]
    assert _DISTINCT_MODEL not in call["prompt"]
    assert _DISTINCT_MODEL not in str(call["schema"])
    assert _DISTINCT_CTX_LABEL not in call["prompt"]


def test_all_roles_drive_the_same_base_executor_object():
    """Through the DEFAULT worker factory, every role's turn hits the SAME base-executor object.

    There is no per-role engine — the ``id(self)`` recorded by each ``run`` call is identical across
    planner, coder, and reviewer, even though each declares a different ``model``.
    """
    rec = _RecordingExecutor()
    factory = _default_worker_factory("BASE-PROMPT", rec)

    for role, model in (("planner", "model-A"), ("coder", "model-B"), ("reviewer", "model-C")):
        agent = RoleAgent(role=role, model=model, prompt_template=f"TMPL-{role}")
        worker = factory(agent)
        schema = _decide_schema() if role == "coder" else None
        worker.run_turn(role=role, inbox=[], schema=schema, tier="working")

    assert len(rec.calls) == 3
    same_object_ids = {c["self"] for c in rec.calls}
    assert same_object_ids == {id(rec)}, "every role must drive the SAME base executor object"
    # And each role only carried its own prompt_template — differently-prompted, one engine.
    assert "TMPL-planner" in rec.calls[0]["prompt"]
    assert "TMPL-coder" in rec.calls[1]["prompt"]
    assert "TMPL-reviewer" in rec.calls[2]["prompt"]


def test_full_executor_run_uses_one_base_engine_for_every_role():
    """End-to-end through ``MultiAgentExecutor.run`` (default factory): a planner/coder/reviewer
    conversation runs EVERY role through the single recording executor, and no role's declared model
    string appears in any ``run`` call. Locks the whole path, not just the worker in isolation.
    """
    rec = _RecordingExecutor()
    cfg = RoleConfig(agents={
        "planner": RoleAgent(role="planner", model="planner-m", prompt_template="P-TMPL"),
        "coder": RoleAgent(role="coder", model="coder-m", prompt_template="C-TMPL"),
        # Independent reviewer needs a distinct effective model OR context; give it distinct context
        # so construction succeeds — that context still must NOT reach the prompt.
        "reviewer": RoleAgent(role="reviewer", model="rev-m", context=(_DISTINCT_CTX_LABEL,),
                              prompt_template="R-TMPL", independent=True),
    })
    ex = MultiAgentExecutor(base_executor=rec, role_config=cfg, instance_id="i",
                            delivery=SubprocessWakeDelivery())

    decision, _ = ex.run("PROMPT", _decide_schema(), tier="reasoning")

    assert decision == {"kind": "research", "plan": {"steps": []}}
    # Three roles => three runs, all on the same object.
    assert len(rec.calls) == 3
    assert {c["self"] for c in rec.calls} == {id(rec)}
    # No declared per-role model string leaked into any call argument.
    joined = " ".join(c["prompt"] for c in rec.calls) + " ".join(str(c["schema"]) for c in rec.calls)
    for model in ("planner-m", "coder-m", "rev-m"):
        assert model not in joined
    # The reviewer's declared context label never reached the prompt, even though it differentiated
    # the independence check enough to construct.
    assert _DISTINCT_CTX_LABEL not in joined
    # ...but the independence check DID honor the declared model/context (kept intact by this task).
    assert ex.reviewer_is_independent("reviewer") is True
