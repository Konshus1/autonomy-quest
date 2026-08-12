"""Step 3 Slice 0 — the workflow-behavior seam is PROVABLY INERT.

The keystone here is byte-identity: `DefaultBehavior` must reproduce today's
`(prompt, schema, tier)` triples VERBATIM, and the loop must construct the seam
without calling it (the `prompts.*` call sites stay unflipped in Slice 0). If any
of that drifts, wiring the seam into the loop later would silently change what the
loop emits — which is exactly what this slice promises will not happen.
"""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner import prompts
from runner.config import Instance, Workflow
from runner.db import Work
from runner.loop import Loop
from runner.workflow_behavior import (
    DefaultBehavior,
    StageBehavior,
    resolve_behavior,
)


ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Frozen fixtures — the exact arg shapes the loop's call sites pass.
# --------------------------------------------------------------------------- #

def _world():
    mission = SimpleNamespace(
        objective="reach 60 verified records",
        measure=SimpleNamespace(what="verified records"),
        horizon="30 days",
        boundaries=SimpleNamespace(
            may_act_alone=["research", "enrich"],
            must_ask_first=["spend money", "email a human"],
        ),
    )
    return {
        "mission": mission,
        "now": 12,
        "target": 60,
        "satisfied": False,
        "learnings": [
            {"insight": "batching 20 models per cycle beats one per cycle", "confidence": 0.9},
            {"insight": "some prospects have stale emails", "confidence": 0.4},
        ],
        "recent_runs": [
            {"summary": "researched 20 models", "outcome": "added 20 records"},
        ],
        "parked": [],
        "pending_replans": [],
        "intent_contract": "concern-1: verified_records >= 60",
    }


def _work(**overrides):
    values = dict(
        id=1,
        kind="research",
        summary="research 20 models",
        rationale="moves the verified-records number",
        plan={"goal_predicate": "verified_records >= 60"},
    )
    values.update(overrides)
    return Work(**values)


def _boundaries():
    return SimpleNamespace(
        may_act_alone=["research", "enrich"],
        must_ask_first=["spend money", "email a human"],
    )


def _prior():
    return [
        {"insight": "batching wins", "confidence": 0.9},
        {"insight": "stale emails exist", "confidence": 0.4},
    ]


TEMPLATE = "running-a-business"
GUIDANCE = "prioritize verification of the oldest records"
OUTCOME = "added 20 verified records"


# --------------------------------------------------------------------------- #
# GOLDEN: DefaultBehavior reproduces today's triples byte-identically.
# --------------------------------------------------------------------------- #

def test_default_decide_is_byte_identical_to_prompts_decide():
    world = _world()
    prompt, schema, tier = DefaultBehavior().decide(world, TEMPLATE, GUIDANCE)

    expected_prompt = prompts.decide(world, TEMPLATE, guidance=GUIDANCE)
    assert prompt == expected_prompt          # character-for-character identity
    assert schema is prompts.DECIDE_SCHEMA     # the SAME schema object, not a copy
    assert tier == "reasoning"


def test_default_act_is_byte_identical_to_prompts_act():
    work, boundaries = _work(), _boundaries()
    prompt, schema, tier = DefaultBehavior().act(work, boundaries)

    expected_prompt = prompts.act(work, boundaries)
    assert prompt == expected_prompt
    assert schema is prompts.ACT_SCHEMA
    assert tier == "working"


def test_default_reflect_is_byte_identical_to_prompts_reflect():
    work, prior = _work(), _prior()
    prompt, schema, tier = DefaultBehavior().reflect(work, OUTCOME, True, prior)

    expected_prompt = prompts.reflect(work, OUTCOME, True, prior)
    assert prompt == expected_prompt
    assert schema is prompts.REFLECT_SCHEMA
    assert tier == "reasoning"


def test_default_reflect_carries_succeeded_flag_verbatim():
    # succeeded is part of the identity — a False run must match prompts.reflect too.
    work, prior = _work(), _prior()
    prompt, _, _ = DefaultBehavior().reflect(work, "nothing changed", False, prior)
    assert prompt == prompts.reflect(work, "nothing changed", False, prior)


def test_triples_are_the_three_element_executor_shape():
    world, work = _world(), _work()
    for triple in (
        DefaultBehavior().decide(world, TEMPLATE, GUIDANCE),
        DefaultBehavior().act(work, _boundaries()),
        DefaultBehavior().reflect(work, OUTCOME, True, _prior()),
    ):
        assert isinstance(triple, tuple) and len(triple) == 3
        prompt, schema, tier = triple
        assert isinstance(prompt, str)
        assert isinstance(schema, dict)
        assert isinstance(tier, str)


# --------------------------------------------------------------------------- #
# resolve_behavior: default AND non-default both map to DefaultBehavior (Slice 0).
# --------------------------------------------------------------------------- #

def test_resolve_default_identity_returns_default_behavior():
    wf = SimpleNamespace(identity="default/v1")
    assert isinstance(resolve_behavior(wf), DefaultBehavior)


def test_resolve_non_default_identity_also_returns_default_behavior_in_slice0():
    # Slice 0 has no YamlBehavior yet: a non-default workflow is runtime-
    # indistinguishable from default. Slice 1 replaces this branch.
    wf = SimpleNamespace(identity="custom/v2")
    behavior = resolve_behavior(wf)
    assert isinstance(behavior, DefaultBehavior)
    assert type(behavior) is DefaultBehavior


def test_resolved_behavior_satisfies_the_stagebehavior_protocol():
    assert isinstance(resolve_behavior(SimpleNamespace(identity="default/v1")), StageBehavior)


# --------------------------------------------------------------------------- #
# Loop.__init__ constructs the seam — and NOTHING calls it yet (it is inert).
# --------------------------------------------------------------------------- #

def test_loop_init_sets_behavior_for_default_instance(monkeypatch):
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(ROOT / "workflows"))
    inst = Instance.load(str(ROOT / "templates/running-a-business/instance.yaml"))
    assert inst.workflow.identity == "default/v1"

    fake_db = SimpleNamespace()
    fake_executor = SimpleNamespace()
    loop = Loop(inst, fake_db, fake_executor)

    assert isinstance(loop.behavior, DefaultBehavior)


def test_seam_is_inert_no_cycle_path_calls_behavior():
    # The proof of inertness: Slice 0 constructs self.behavior but flips no call
    # site. Assert the loop source never invokes a behavior stage method — the
    # only reference to self.behavior is the assignment in __init__.
    src = (ROOT / "runner" / "loop.py").read_text()
    for method in ("self.behavior.decide", "self.behavior.act", "self.behavior.reflect"):
        assert method not in src, f"Slice 0 must not call {method} — the seam is inert"
    # The attribute is constructed exactly once and never invoked.
    assert src.count("self.behavior = resolve_behavior(inst.workflow)") == 1
    assert "self.behavior(" not in src


def test_prompts_call_sites_remain_unflipped_in_loop():
    # The stock call sites are the source of truth in Slice 0 — assert they are
    # still present and untouched, so DefaultBehavior's golden match is meaningful.
    src = (ROOT / "runner" / "loop.py").read_text()
    assert "prompts.decide(world, self.inst.template, guidance=verdict.guidance)" in src
    assert "prompts.act(work, self.inst.mission.boundaries), prompts.ACT_SCHEMA, tier=\"working\"" in src
    assert "prompts.REFLECT_SCHEMA" in src
