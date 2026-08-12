"""Workflow-behavior seam (Step 3).

This module owns how a workflow turns each stage's inputs into the
`(prompt, schema, tier)` triple the executor consumes. The loop constructs one
`StageBehavior` per instance (`resolve_behavior(inst.workflow)`) and routes the
DECIDE / ACT / REFLECT stages through it.

Two implementations exist:

* ``DefaultBehavior`` — reproduces today's stock prompts VERBATIM. Routing the
  default workflow through it is a no-op; ``tests/test_workflow_behavior.py``
  proves byte-identity so the Slice 1 call-site flip changed nothing the live
  default loop emits.

* ``YamlBehavior`` — prompt-only interpretation for a NON-default workflow. It
  renders PROMPT TEXT from templates the workflow declares, and NOTHING ELSE.
  The schema and tier stay CANONICAL (the loop's, not the workflow's), so a
  non-default workflow can change only what the agent is told — never the
  executor contract, the stage set, any gate, or the output keys the loop
  consumes. Templates are DATA: yaml/markdown scalars rendered against a fixed,
  whitelisted variable set per stage. There is no import / eval / exec / dotted
  path to code, and every placeholder is validated AT LOAD (fail-closed).

THE SAFETY INVARIANT: a non-default workflow can change ONLY the prompt text. It
cannot alter the stage set/order, observe/learn, any gate
(consequence/blast-radius/human/budget/acquisition-receipt/governance/intent),
or the required output keys the loop consumes. Those all live in the loop and in
the canonical schemas, which YamlBehavior returns by reference and never mutates.
A prompt cannot talk the loop out of a gate: the gates read RE-DERIVED
ground-truth numbers (the plan's own step blast/expense, measures re-read from
the DB, autonomy/reversibility facts), not the prompt text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

import yaml

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
    so routing the loop through this behavior is a no-op for the default workflow.
    """

    def decide(self, world: dict, template: str, guidance: str) -> Triple:
        # Mirrors loop.py DECIDE:
        #   prompts.decide(world, self.inst.template, guidance=verdict.guidance),
        #   prompts.DECIDE_SCHEMA, tier="reasoning"
        return (
            prompts.decide(world, template, guidance=guidance),
            prompts.DECIDE_SCHEMA,
            "reasoning",
        )

    def act(self, work: Any, boundaries: Any) -> Triple:
        # Mirrors loop.py ACT:
        #   prompts.act(work, self.inst.mission.boundaries), prompts.ACT_SCHEMA, tier="working"
        return (
            prompts.act(work, boundaries),
            prompts.ACT_SCHEMA,
            "working",
        )

    def reflect(self, work: Any, outcome: str, succeeded: bool, prior: Any) -> Triple:
        # Mirrors loop.py REFLECT / recovery-REFLECT:
        #   prompts.reflect(work, outcome, succeeded, self.db.live_learnings(limit=50)),
        #   prompts.REFLECT_SCHEMA, tier="reasoning"
        return (
            prompts.reflect(work, outcome, succeeded, prior),
            prompts.REFLECT_SCHEMA,
            "reasoning",
        )


# --------------------------------------------------------------------------- #
# Prompt-template interpretation for NON-default workflows.
#
# Templates are DATA rendered against a fixed, WHITELISTED variable set per
# stage. A placeholder is `{name}` where `name` is exactly one whitelisted key —
# no attribute access, no indexing, no dotted path, no format spec, no code. Any
# `{...}` that is not a whitelisted name is rejected AT LOAD (fail-closed), so an
# unknown or unsafe placeholder can never reach a live cycle.
# --------------------------------------------------------------------------- #

# The stages a workflow may override, mapped to the placeholders each may use.
# A behavior block on any OTHER stage (observe / learn) is rejected loudly: those
# are loop-owned and interpreting them would let a workflow reshape the pipeline.
DECIDE_VARS: frozenset[str] = frozenset({
    "mission_objective", "measure_what", "now", "target", "horizon",
    "template", "guidance", "may_act_alone", "must_ask_first", "intent_contract",
})
ACT_VARS: frozenset[str] = frozenset({
    "work_summary", "work_rationale", "work_plan", "may_act_alone", "must_ask_first",
})
REFLECT_VARS: frozenset[str] = frozenset({
    "work_summary", "outcome", "succeeded", "prior_known",
})
STAGE_VARS: dict[str, frozenset[str]] = {
    "decide": DECIDE_VARS,
    "act": ACT_VARS,
    "reflect": REFLECT_VARS,
}

# Every `{...}` occurrence, whether or not the inner text is a valid name. We
# validate the inner text against the whitelist so `{now.upper}`, `{Now}`, and
# `{anything}` all fail closed rather than being silently left as literal text.
_BRACE_RE = re.compile(r"\{([^{}]*)\}")
# A valid placeholder name: lowercase identifier, exactly one whitelisted key.
_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _validate_template(text: str, allowed: frozenset[str], where: str) -> None:
    """Reject any placeholder that is not exactly one whitelisted name. LOAD-time."""
    for raw in _BRACE_RE.findall(text):
        name = raw.strip()
        if not _NAME_RE.match(name) or name not in allowed:
            raise ValueError(
                f"unknown or unsafe placeholder {{{raw}}} in {where}; "
                f"templates are data — allowed placeholders: {sorted(allowed)}"
            )


def _render(text: str, variables: Mapping[str, str]) -> str:
    """Substitute whitelisted `{name}` placeholders with pre-stringified values.

    Only names validated at load appear, and `variables` always carries the full
    whitelist, so this never touches attributes, indices, or code — it is a plain
    name -> string substitution.
    """
    return _BRACE_RE.sub(lambda m: variables[m.group(1).strip()], text)


def _decide_vars(world: dict, template: str, guidance: str) -> dict[str, str]:
    m = world["mission"]
    return {
        "mission_objective": str(m.objective),
        "measure_what": str(m.measure.what),
        "now": str(world.get("now")),
        "target": str(world.get("target")),
        "horizon": str(m.horizon),
        "template": str(template),
        "guidance": str(guidance or ""),
        "may_act_alone": str(m.boundaries.may_act_alone),
        "must_ask_first": str(m.boundaries.must_ask_first),
        "intent_contract": str(world.get("intent_contract", "")),
    }


def _act_vars(work: Any, boundaries: Any) -> dict[str, str]:
    return {
        "work_summary": str(work.summary),
        "work_rationale": str(work.rationale),
        "work_plan": str(work.plan),
        "may_act_alone": str(boundaries.may_act_alone),
        "must_ask_first": str(boundaries.must_ask_first),
    }


def _reflect_vars(work: Any, outcome: str, succeeded: bool, prior: Any) -> dict[str, str]:
    known = "\n".join(f"- {p.get('insight', '')}" for p in (prior or [])) or "(nothing yet)"
    return {
        "work_summary": str(work.summary),
        "outcome": str(outcome),
        "succeeded": str(succeeded),
        "prior_known": known,
    }


class YamlBehavior:
    """Prompt-only interpretation of a non-default workflow.

    Holds one rendered-at-cycle template per OVERRIDDEN stage. A stage with no
    template delegates to ``DefaultBehavior`` so it stays byte-identical to the
    stock loop. The schema and tier are ALWAYS the canonical loop values — a
    workflow cannot change them (that is Slice 2's scope, and even then only
    under a whitelist). This keeps the safety invariant airtight in Slice 1:
    the only thing a non-default workflow changes is prompt text.
    """

    def __init__(self, templates: dict[str, str]) -> None:
        # templates maps stage id -> validated template string; only for stages
        # that declared a behavior block. Everything else falls through to stock.
        self._templates = dict(templates)
        self._default = DefaultBehavior()

    def declares_any(self) -> bool:
        return bool(self._templates)

    def decide(self, world: dict, template: str, guidance: str) -> Triple:
        tmpl = self._templates.get("decide")
        if tmpl is None:
            return self._default.decide(world, template, guidance)
        return (_render(tmpl, _decide_vars(world, template, guidance)),
                prompts.DECIDE_SCHEMA, "reasoning")

    def act(self, work: Any, boundaries: Any) -> Triple:
        tmpl = self._templates.get("act")
        if tmpl is None:
            return self._default.act(work, boundaries)
        return (_render(tmpl, _act_vars(work, boundaries)),
                prompts.ACT_SCHEMA, "working")

    def reflect(self, work: Any, outcome: str, succeeded: bool, prior: Any) -> Triple:
        tmpl = self._templates.get("reflect")
        if tmpl is None:
            return self._default.reflect(work, outcome, succeeded, prior)
        return (_render(tmpl, _reflect_vars(work, outcome, succeeded, prior)),
                prompts.REFLECT_SCHEMA, "reasoning")

    @classmethod
    def from_workflow(cls, workflow: Any) -> "YamlBehavior":
        """Load, validate, and compile a workflow's per-stage prompt templates.

        Fails LOUD (ValueError) if: a behavior block sits on a non-overridable
        stage (observe/learn); a prompt_template path escapes the workflow's own
        directory (``../`` or a symlink out); the file is missing; or a template
        carries an unknown/unsafe placeholder. None of these can slip to runtime.
        """
        source = Path(workflow.source)
        wf_root = source.parent.resolve()   # workflows/<name>/vN/
        raw = yaml.safe_load(source.read_text()) or {}

        templates: dict[str, str] = {}
        for stage in raw.get("stages") or []:
            behavior = stage.get("behavior")
            if behavior is None:
                continue
            sid = stage.get("id")
            if sid not in STAGE_VARS:
                raise ValueError(
                    f"workflow {workflow.identity}: stage {sid!r} may not declare a "
                    f"behavior block — only decide/act/reflect may. observe/learn are "
                    f"loop-owned and interpreting them would reshape the pipeline."
                )
            if not isinstance(behavior, Mapping):
                raise ValueError(
                    f"workflow {workflow.identity}: stage {sid!r} behavior must be a mapping")
            rel = behavior.get("prompt_template")
            if not rel or not isinstance(rel, str):
                raise ValueError(
                    f"workflow {workflow.identity}: stage {sid!r} behavior requires a "
                    f"'prompt_template' relative path")
            # Same traversal guard the workflow selector uses (config.py): resolve
            # the path (following symlinks) and require the workflow's own dir to
            # be an ancestor. `../` or a symlink pointing outside resolves outside
            # wf_root and is rejected.
            tmpl_path = (wf_root / rel).resolve()
            if wf_root not in tmpl_path.parents:
                raise ValueError(
                    f"workflow {workflow.identity}: prompt_template {rel!r} escapes the "
                    f"workflow root {wf_root}")
            if not tmpl_path.is_file():
                raise ValueError(
                    f"workflow {workflow.identity}: prompt_template not found: {tmpl_path}")
            text = tmpl_path.read_text()
            _validate_template(text, STAGE_VARS[sid], f"{workflow.identity} stage {sid!r} ({rel})")
            templates[sid] = text
        return cls(templates)


def resolve_behavior(workflow: Any) -> StageBehavior:
    """Pick the StageBehavior for a workflow.

    * ``default/v1`` ALWAYS maps to ``DefaultBehavior`` — the live default loop is
      byte-identical, no interpretation path is even constructed for it.
    * A non-default workflow that declares per-stage behavior blocks maps to a
      ``YamlBehavior`` interpreting them (prompt text only).
    * A non-default workflow that declares NO behavior blocks maps to
      ``DefaultBehavior`` — it runs the stock prompts, just under its own identity.
    """
    if workflow.identity == "default/v1":
        return DefaultBehavior()
    behavior = YamlBehavior.from_workflow(workflow)
    if not behavior.declares_any():
        return DefaultBehavior()
    return behavior
