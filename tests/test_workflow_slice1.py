"""Step 3 Slice 1 — the workflow-behavior seam is LIVE, and safe.

Keystones proved here:

* END-TO-END DEFAULT EQUIVALENCE — one full cycle() on default/v1 produces
  IDENTICAL executor inputs and DB side effects whether the DECIDE/ACT/REFLECT
  stages go through the post-flip behavior seam (DefaultBehavior) or through a
  shim reproducing the exact PRE-FLIP direct `prompts.*` calls. The flip changed
  nothing the live default loop emits.
* YamlBehavior INTERPRETS a non-default workflow into DIFFERENT prompt TEXT while
  the schema and tier stay canonical (the loop's, by reference).
* SELECTOR + TEMPLATE PATH SAFETY — a guest-settable selector cannot escape the
  workflow root, load a non-deterministic or wrong-stage-set workflow, or point a
  prompt_template outside its own dir. The env selector wins over yaml.
* GATE PRESERVATION ON THE INTERPRETED PATH — the same gate scenarios fire
  identically on the interpreted (research-loop) path as on default: a plan that
  under-states nothing structurally but rides an interpreted prompt still gets
  gated on blast, still parks over the per-plan approval ceiling, and the budget
  hard cap still stops the loop. A prompt cannot talk the loop out of a gate.
* LOADER VALIDATION — a behavior block on observe/learn is rejected loudly; an
  unknown template placeholder fails closed AT LOAD, not at cycle time.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner import prompts
from runner.budget import BudgetExceeded
from runner.config import (
    Autonomy, Boundaries, BudgetCfg, Curiosity, Engine, Instance, Measure,
    Mission, Models, Money, Surfaces, Workflow,
)
from runner.executor import Usage
from runner.loop import Loop
from runner.workflow_behavior import (
    DefaultBehavior, YamlBehavior, resolve_behavior,
)

from tests.test_loop_approval_execution import FakeDb, instance
from tests.test_pre_act_predictions import ParkDb, PlannedExecutor


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "workflows"


# --------------------------------------------------------------------------- #
# Shared fixtures for the interpreted-path tests.
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
        "mission": mission, "now": 12, "target": 60, "satisfied": False,
        "learnings": [{"insight": "batching wins", "confidence": 0.9}],
        "recent_runs": [], "parked": [], "pending_replans": [],
        "intent_contract": "concern-1: verified_records >= 60",
    }


TEMPLATE = "running-a-business"
GUIDANCE = "prioritize verification of the oldest records"


class PreFlipShim:
    """Reproduces the EXACT pre-Slice-1 call sites: direct `prompts.*`, canonical
    schema, canonical tier. Standing in for `self.behavior`, it makes the loop run
    the code path that existed BEFORE the flip — so a byte-identical DB/executor
    result versus DefaultBehavior proves the flip is behavior-preserving."""

    def decide(self, world, template, guidance):
        return prompts.decide(world, template, guidance=guidance), prompts.DECIDE_SCHEMA, "reasoning"

    def act(self, work, boundaries):
        return prompts.act(work, boundaries), prompts.ACT_SCHEMA, "working"

    def reflect(self, work, outcome, succeeded, prior):
        return prompts.reflect(work, outcome, succeeded, prior), prompts.REFLECT_SCHEMA, "reasoning"


class RecordDb(FakeDb):
    """FakeDb that also records heartbeat rows (the loop writes one per completed run)."""

    def __init__(self):
        super().__init__()
        self.beats = []

    def park_for_human(self, work_id, note=None):
        self.events.append(("park", note))

    def beat(self, state, detail="", retry_after_s=None):
        self.beats.append((state, detail, retry_after_s))


class RecordingExecutor:
    """Wraps a deterministic executor and records every (prompt, schema, tier) it is fed."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    def run(self, prompt, schema, tier="working"):
        self.calls.append((prompt, schema, tier))
        return self.inner.run(prompt, schema, tier)


def _completed_projection(db):
    # Compare the deterministic fields of complete_run (drop nothing nondeterministic:
    # plan_id is a uuid and never reaches complete_run, so the full kw is stable).
    return [(run_id, {k: kw[k] for k in sorted(kw)}) for run_id, kw in db.completed]


# --------------------------------------------------------------------------- #
# END-TO-END DEFAULT EQUIVALENCE — the flip is behavior-preserving for default.
# --------------------------------------------------------------------------- #

def test_full_cycle_default_is_identical_before_and_after_the_flip(monkeypatch):
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(WORKFLOWS))

    # POST-FLIP: the loop routes DECIDE/ACT/REFLECT through the real behavior seam,
    # which for default/v1 is DefaultBehavior.
    db_after = RecordDb()
    ex_after = RecordingExecutor(PlannedExecutor(db_after))
    loop_after = Loop(instance(), db_after, ex_after)
    assert isinstance(loop_after.behavior, DefaultBehavior)
    cycle_after = loop_after.cycle()

    # PRE-FLIP: same loop, but self.behavior is the shim reproducing the original
    # direct `prompts.*` call sites — the code path that existed before Slice 1.
    db_before = RecordDb()
    ex_before = RecordingExecutor(PlannedExecutor(db_before))
    loop_before = Loop(instance(), db_before, ex_before)
    loop_before.behavior = PreFlipShim()
    cycle_before = loop_before.cycle()

    assert cycle_after is not None and cycle_before is not None

    # The executor was fed byte-identical (prompt, schema, tier) triples — the
    # sharpest proof that the flip changed nothing the loop emits.
    assert ex_after.calls == ex_before.calls
    assert [schema for _, schema, _ in ex_after.calls] == [
        prompts.DECIDE_SCHEMA, prompts.ACT_SCHEMA, prompts.REFLECT_SCHEMA]

    # And the DB side effects — complete_run / write_learning / beat rows — match.
    assert _completed_projection(db_after) == _completed_projection(db_before)
    assert db_after.learned == db_before.learned
    assert db_after.beats == db_before.beats
    assert db_after.beats and db_before.beats   # a heartbeat really was written


# --------------------------------------------------------------------------- #
# YamlBehavior: DIFFERENT prompt text, CANONICAL schema + tier.
# --------------------------------------------------------------------------- #

def test_non_default_yaml_behavior_differs_only_in_prompt_text(monkeypatch):
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(WORKFLOWS))
    wf = Workflow.load("research-loop/v1")
    behavior = resolve_behavior(wf)
    assert isinstance(behavior, YamlBehavior)

    world = _world()
    work = SimpleNamespace(summary="research 20 models", rationale="moves the number",
                           plan={"goal_predicate": "x>=60"})
    boundaries = world["mission"].boundaries
    prior = [{"insight": "batching wins", "confidence": 0.9}]
    default = DefaultBehavior()

    for interp, stock, canonical_schema, canonical_tier in (
        (behavior.decide(world, TEMPLATE, GUIDANCE),
         default.decide(world, TEMPLATE, GUIDANCE), prompts.DECIDE_SCHEMA, "reasoning"),
        (behavior.act(work, boundaries),
         default.act(work, boundaries), prompts.ACT_SCHEMA, "working"),
        (behavior.reflect(work, "added 20", True, prior),
         default.reflect(work, "added 20", True, prior), prompts.REFLECT_SCHEMA, "reasoning"),
    ):
        prompt, schema, tier = interp
        assert prompt != stock[0]              # prompt TEXT differs
        assert "RESEARCH-LOOP" in prompt        # it is the workflow's own template
        assert schema is canonical_schema       # SAME schema object — not the workflow's
        assert tier == canonical_tier           # canonical tier, fixed by the loop


def test_yaml_behavior_renders_whitelisted_variables(monkeypatch):
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(WORKFLOWS))
    behavior = resolve_behavior(Workflow.load("research-loop/v1"))
    prompt, _, _ = behavior.decide(_world(), TEMPLATE, GUIDANCE)
    # Whitelisted values are substituted; no raw placeholder braces survive.
    assert "reach 60 verified records" in prompt   # {mission_objective}
    assert GUIDANCE in prompt                        # {guidance}
    assert "{" not in prompt and "}" not in prompt


# --------------------------------------------------------------------------- #
# SELECTOR + TEMPLATE PATH SAFETY.
# --------------------------------------------------------------------------- #

def test_env_selector_traversal_is_rejected(monkeypatch):
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(WORKFLOWS))
    monkeypatch.setenv("AQ_WORKFLOW_SELECTOR", "../escape/v1")
    with pytest.raises(ValueError):
        Instance.load(str(ROOT / "templates/running-a-business/instance.yaml"))


def test_selector_escaping_the_root_via_dotdot_name_is_rejected(monkeypatch):
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(WORKFLOWS))
    with pytest.raises(ValueError, match="escapes the workflow root"):
        Workflow.load("../v1")


def test_env_selector_at_non_deterministic_workflow_is_rejected(tmp_path, monkeypatch):
    wf_dir = tmp_path / "loose" / "v1"
    wf_dir.mkdir(parents=True)
    (wf_dir / "workflow.yaml").write_text(
        "name: loose\nversion: 1\ndeterministic: false\n"
        "stages:\n" + "".join(f"  - id: {s}\n" for s in
                               ("observe", "decide", "act", "reflect", "learn")))
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="must implement deterministic"):
        Workflow.load("loose/v1")


def test_env_selector_at_wrong_stage_set_is_rejected(tmp_path, monkeypatch):
    wf_dir = tmp_path / "short" / "v1"
    wf_dir.mkdir(parents=True)
    (wf_dir / "workflow.yaml").write_text(
        "name: short\nversion: 1\ndeterministic: true\n"
        "stages:\n" + "".join(f"  - id: {s}\n" for s in ("observe", "decide", "act")))
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="must implement deterministic"):
        Workflow.load("short/v1")


def test_prompt_template_escaping_the_workflow_root_is_rejected(tmp_path, monkeypatch):
    (tmp_path / "SECRET.md").write_text("{now}")   # a file OUTSIDE the workflow dir
    wf_dir = tmp_path / "sneaky" / "v1"
    wf_dir.mkdir(parents=True)
    (wf_dir / "workflow.yaml").write_text(
        "name: sneaky\nversion: 1\ndeterministic: true\nstages:\n"
        "  - id: observe\n"
        "  - id: decide\n    behavior:\n      prompt_template: ../../SECRET.md\n"
        "  - id: act\n  - id: reflect\n  - id: learn\n")
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(tmp_path))
    wf = Workflow.load("sneaky/v1")
    with pytest.raises(ValueError, match="escapes the workflow root"):
        resolve_behavior(wf)


def test_prompt_template_symlink_escape_is_rejected(tmp_path, monkeypatch):
    outside = tmp_path / "outside.md"
    outside.write_text("{now}")
    wf_dir = tmp_path / "linky" / "v1"
    (wf_dir / "prompts").mkdir(parents=True)
    link = wf_dir / "prompts" / "decide.md"
    link.symlink_to(outside)   # a symlink pointing OUT of the workflow root
    (wf_dir / "workflow.yaml").write_text(
        "name: linky\nversion: 1\ndeterministic: true\nstages:\n"
        "  - id: observe\n"
        "  - id: decide\n    behavior:\n      prompt_template: prompts/decide.md\n"
        "  - id: act\n  - id: reflect\n  - id: learn\n")
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(tmp_path))
    wf = Workflow.load("linky/v1")
    with pytest.raises(ValueError, match="escapes the workflow root"):
        resolve_behavior(wf)


def test_valid_env_selector_wins_over_yaml(monkeypatch):
    # instance.yaml explicitly says `workflow: default/v1`; the env selector must win.
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(WORKFLOWS))
    monkeypatch.setenv("AQ_WORKFLOW_SELECTOR", "research-loop/v1")
    inst = Instance.load(str(ROOT / "templates/running-a-business/instance.yaml"))
    assert inst.workflow.identity == "research-loop/v1"


# --------------------------------------------------------------------------- #
# LOADER VALIDATION: behavior only on decide/act/reflect; placeholders fail closed.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("stage", ["observe", "learn"])
def test_behavior_block_on_observe_or_learn_is_rejected(tmp_path, monkeypatch, stage):
    wf_dir = tmp_path / "badstage" / "v1"
    (wf_dir / "prompts").mkdir(parents=True)
    (wf_dir / "prompts" / "x.md").write_text("hello")
    lines = ["name: badstage", "version: 1", "deterministic: true", "stages:"]
    for s in ("observe", "decide", "act", "reflect", "learn"):
        lines.append(f"  - id: {s}")
        if s == stage:
            lines += ["    behavior:", "      prompt_template: prompts/x.md"]
    (wf_dir / "workflow.yaml").write_text("\n".join(lines) + "\n")
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(tmp_path))
    wf = Workflow.load("badstage/v1")
    with pytest.raises(ValueError, match="may not declare a behavior block"):
        resolve_behavior(wf)


def test_unknown_placeholder_fails_closed_at_load(tmp_path, monkeypatch):
    wf_dir = tmp_path / "typo" / "v1"
    (wf_dir / "prompts").mkdir(parents=True)
    # `{no_such_var}` is not in the decide whitelist -> reject AT LOAD.
    (wf_dir / "prompts" / "decide.md").write_text("Mission: {mission_objective}\nBad: {no_such_var}\n")
    (wf_dir / "workflow.yaml").write_text(
        "name: typo\nversion: 1\ndeterministic: true\nstages:\n"
        "  - id: observe\n"
        "  - id: decide\n    behavior:\n      prompt_template: prompts/decide.md\n"
        "  - id: act\n  - id: reflect\n  - id: learn\n")
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(tmp_path))
    wf = Workflow.load("typo/v1")
    with pytest.raises(ValueError, match="unknown or unsafe placeholder"):
        resolve_behavior(wf)


def test_attribute_access_placeholder_is_rejected(tmp_path, monkeypatch):
    # A dotted/attribute placeholder must never render — it is not a whitelisted name.
    wf_dir = tmp_path / "dotted" / "v1"
    (wf_dir / "prompts").mkdir(parents=True)
    (wf_dir / "prompts" / "decide.md").write_text("Escape: {now.__class__}\n")
    (wf_dir / "workflow.yaml").write_text(
        "name: dotted\nversion: 1\ndeterministic: true\nstages:\n"
        "  - id: observe\n"
        "  - id: decide\n    behavior:\n      prompt_template: prompts/decide.md\n"
        "  - id: act\n  - id: reflect\n  - id: learn\n")
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(tmp_path))
    wf = Workflow.load("dotted/v1")
    with pytest.raises(ValueError, match="unknown or unsafe placeholder"):
        resolve_behavior(wf)


# --------------------------------------------------------------------------- #
# GATE PRESERVATION ON THE INTERPRETED PATH.
#
# Each gate scenario runs on BOTH the default and the interpreted (research-loop)
# workflow. Identical gating on both proves the interpreted prompt cannot weaken
# a gate: the gate reads RE-DERIVED ground-truth (the plan's own structured
# blast/expense, the budget re-read from the DB), never the prompt text.
# --------------------------------------------------------------------------- #

WORKFLOWS_UNDER_TEST = ["default/v1", "research-loop/v1"]


def _loop_on(selector, db, ex, monkeypatch, *, autonomy=None, money=None):
    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(WORKFLOWS))
    inst = instance()
    inst.workflow = Workflow.load(selector)
    if autonomy is not None:
        inst.budget.autonomy = autonomy
    if money is not None:
        inst.budget.money = money
    return Loop(inst, db, ex)


@pytest.mark.parametrize("selector", WORKFLOWS_UNDER_TEST)
def test_understated_blast_prompt_is_still_gated(selector, monkeypatch):
    # A plan whose steps affect 100 entities is blast level 3. No prompt — default
    # or the interpreted research-loop prompt — changes that the gate re-derives
    # the level from the plan's own facts and PARKS for human review.
    db = ParkDb()
    ex = PlannedExecutor(db, blast_count=100)
    loop = _loop_on(selector, db, ex, monkeypatch)
    result = loop.cycle()
    assert result is None                    # gated, not executed
    assert "park" in db.events               # parked for the human
    assert "act" not in db.events            # the ACT never ran


@pytest.mark.parametrize("selector", WORKFLOWS_UNDER_TEST)
def test_over_per_plan_approval_still_parks(selector, monkeypatch):
    # Expense over the per-plan ceiling parks — on the interpreted path too.
    class Expensive(PlannedExecutor):
        def run(self, prompt, schema, tier="working"):
            out, usage = super().run(prompt, schema, tier)
            if schema is prompts.DECIDE_SCHEMA:
                out["plan"]["expected_expense_usd"] = 9.99   # over the $3 ceiling
            return out, usage

    db = ParkDb()
    ex = Expensive(db)
    loop = _loop_on(selector, db, ex, monkeypatch,
                    autonomy=Autonomy(level="act-external", per_plan_approval_usd=3.0,
                                      blast_radius_gate_level=3))
    result = loop.cycle()
    assert result is None
    assert "park" in db.events
    assert "act" not in db.events


@pytest.mark.parametrize("selector", WORKFLOWS_UNDER_TEST)
def test_budget_hard_cap_still_stops_the_interpreted_loop(selector, monkeypatch):
    # The hard cap is re-read from the DB at cycle start and cannot be argued past
    # — no workflow, default or interpreted, gets to spend past it.
    class OverCapDb(ParkDb):
        def sum_cost(self, since):
            return Decimal("999")

    db = OverCapDb()
    ex = PlannedExecutor(db)
    loop = _loop_on(selector, db, ex, monkeypatch,
                    money=Money(daily_soft_usd=0, monthly_hard_usd=100))
    with pytest.raises(BudgetExceeded):
        loop.cycle()


@pytest.mark.parametrize("selector", WORKFLOWS_UNDER_TEST)
def test_acquisition_receipt_violation_still_quarantines(selector, monkeypatch):
    # An autonomous-practice acquisition that returns NO holdout-transfer receipt is
    # quarantined — the receipt check reads the ACT result's structured metrics, not
    # the prompt, so the interpreted (research-loop) act prompt cannot slip past it.
    from tests.test_meta_mode_loop import MetaDb, PracticeMetaExecutor

    monkeypatch.setenv("AQ_WORKFLOWS_DIR", str(WORKFLOWS))
    db = MetaDb()
    ex = PracticeMetaExecutor(db, valid=False)
    inst = instance()
    inst.workflow = Workflow.load(selector)
    loop = Loop(inst, db, ex)
    with pytest.raises(RuntimeError, match="holdout transfer"):
        loop.cycle()
    assert db.acquisitions[0]["status"] == "skipped"
    assert "acquisition_quarantined" in db.events


@pytest.mark.parametrize("selector", WORKFLOWS_UNDER_TEST)
def test_clean_plan_executes_and_learns_on_both_workflows(selector, monkeypatch):
    # The control: an in-bounds plan runs to completion on both — so the gate tests
    # above are showing the GATE firing, not the workflow failing to run at all.
    db = ParkDb()
    ex = PlannedExecutor(db)
    loop = _loop_on(selector, db, ex, monkeypatch)
    result = loop.cycle()
    assert result is not None
    assert "act" in db.events
    assert db.learned          # a learning was written
    assert "park" not in db.events
