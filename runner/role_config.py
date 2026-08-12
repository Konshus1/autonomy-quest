"""Role-agent declaration in the workflow layer (#4834 comms Phase 4, design §6/§10).

A workflow MAY declare multiple ROLE AGENTS (planner / coder / reviewer / evaluator) with a
per-role model / prompt / context. This module PARSES that declaration and decides — together with
an explicit flag — whether the intra-instance multi-agent runtime activates.

THE INERT INVARIANT (design §10 Phase 4):
  * ``default/v1`` and ANY workflow that declares NO ``roles:`` block resolve to ``None`` here — the
    loop takes the EXISTING single-executor path, byte-identical. No runtime is constructed.
  * A workflow that declares roles activates the runtime ONLY when the flag ``AQ_WORKFLOW_MULTI_AGENT``
    is also set. Declaring roles WITHOUT the flag is still inert (single-executor path).

This is the exact analogue of ``resolve_behavior`` in ``workflow_behavior.py``: the workflow LAYER
declares intent; the loop-owned gates and the activation flag stay outside the workflow's control. A
workflow can declare who talks, never what authority the conversation carries — the gates in ``Loop``
run downstream on re-derived ground truth regardless of any role's verdict (see ``multi_agent_executor``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

# The ONLY role names a workflow may declare. There is deliberately no "shell", "command",
# "executor", "host", or "operator" role — roles are conversation participants, never actuators.
ALLOWED_ROLES: frozenset[str] = frozenset({"planner", "coder", "reviewer", "evaluator"})

# The role whose final structured output becomes the DECIDE reply the loop consumes. Preference
# order: an explicit producer is the coder (it emits the plan), falling back to the planner. A
# reviewer/evaluator is NEVER a producer — its output is evidence, never the returned decision.
_PRODUCER_PREFERENCE: tuple[str, ...] = ("coder", "planner")

# The flag that ARMS the runtime. Absent / falsey => inert even for a role-declaring workflow.
ACTIVATION_FLAG = "AQ_WORKFLOW_MULTI_AGENT"

# Fan-out is BOUNDED. A workflow may lower the cap but never raise it past this hard ceiling — an
# unbounded (or large) role spawn is a resource-exhaustion / fork-bomb surface, refused at load.
HARD_FANOUT_CEILING = 8
DEFAULT_FANOUT_CAP = 4

# Delivery adapters a workflow may select. "subprocess" is the DEFAULT deterministic worker; "tmux"
# is the optional interactive-pane adapter and is only ever a DELIVERY mechanism, never the store.
ALLOWED_DELIVERY: frozenset[str] = frozenset({"subprocess", "tmux"})


@dataclass(frozen=True)
class RoleAgent:
    """One declared role agent. Data only — it grants no capability."""

    role: str
    model: str | None = None
    prompt_template: str | None = None
    context: tuple[str, ...] = ()
    # A reviewer/evaluator that is intended to serve as a downstream evidence GATE must be
    # INDEPENDENT: a separately configured principal/model/context (design §8.3, "review opinion =
    # one evidence source"). This flag records the DECLARATION; independence is *enforced* (distinct
    # principal + distinct model/context) by the runtime, which refuses to treat a non-independent
    # reviewer as a gate. Even an independent reviewer is downstream EVIDENCE, never authority.
    independent: bool = False


@dataclass(frozen=True)
class RoleConfig:
    """A workflow's parsed role declaration + bounded runtime parameters."""

    agents: dict[str, RoleAgent]
    fanout_cap: int = DEFAULT_FANOUT_CAP
    delivery: str = "subprocess"
    # Session credentials minted for role principals expire after this many seconds (design §8.2:
    # "session credentials expire"). Teardown revokes them regardless.
    session_ttl_s: int = 900

    def producer_role(self) -> str:
        for pref in _PRODUCER_PREFERENCE:
            if pref in self.agents:
                return pref
        # A role-declaring workflow with neither coder nor planner cannot produce a decision.
        raise ValueError(
            "a multi-agent workflow must declare a 'coder' or 'planner' role to produce the decision; "
            f"declared roles: {sorted(self.agents)}")

    def reviewer_roles(self) -> tuple[str, ...]:
        return tuple(r for r in ("reviewer", "evaluator") if r in self.agents)


def _parse_role(role: str, spec: Any, *, where: str) -> RoleAgent:
    if role not in ALLOWED_ROLES:
        raise ValueError(
            f"{where}: unknown role {role!r}; a workflow may declare only {sorted(ALLOWED_ROLES)} "
            f"(there is deliberately no shell/command/host/operator role)")
    if spec is None:
        spec = {}
    if not isinstance(spec, Mapping):
        raise ValueError(f"{where}: role {role!r} must be a mapping, got {type(spec).__name__}")
    ctx = spec.get("context") or []
    if not isinstance(ctx, (list, tuple)) or any(not isinstance(c, str) for c in ctx):
        raise ValueError(f"{where}: role {role!r} 'context' must be a list of strings")
    model = spec.get("model")
    if model is not None and not isinstance(model, str):
        raise ValueError(f"{where}: role {role!r} 'model' must be a string")
    tmpl = spec.get("prompt_template")
    if tmpl is not None and not isinstance(tmpl, str):
        raise ValueError(f"{where}: role {role!r} 'prompt_template' must be a string path")
    return RoleAgent(
        role=role,
        model=model,
        prompt_template=tmpl,
        context=tuple(ctx),
        independent=bool(spec.get("independent", False)),
    )


def resolve_roles(workflow: Any) -> RoleConfig | None:
    """Parse a workflow's ``roles:`` block, or ``None`` if it declares none.

    Fails LOUD (ValueError) on a malformed declaration (unknown role, fan-out over the hard
    ceiling, unknown delivery adapter) — a broken multi-agent declaration must never silently fall
    back to something surprising. ``default/v1`` short-circuits to ``None`` with no file read at all,
    exactly like ``resolve_behavior``: the default loop constructs no runtime path.
    """
    if getattr(workflow, "identity", None) == "default/v1":
        return None
    source = getattr(workflow, "source", None)
    if not source:
        return None
    raw = yaml.safe_load(Path(source).read_text()) or {}
    roles_block = raw.get("roles")
    if not roles_block:
        return None
    if not isinstance(roles_block, Mapping):
        raise ValueError(f"workflow {workflow.identity}: 'roles' must be a mapping")

    where = f"workflow {workflow.identity} roles"
    agents_raw = roles_block.get("agents")
    if not isinstance(agents_raw, Mapping) or not agents_raw:
        raise ValueError(f"{where}: 'roles.agents' must be a non-empty mapping of role -> spec")
    agents = {role: _parse_role(role, spec, where=where) for role, spec in agents_raw.items()}

    fanout_cap = roles_block.get("fanout_cap", DEFAULT_FANOUT_CAP)
    if not isinstance(fanout_cap, int) or isinstance(fanout_cap, bool) or fanout_cap < 1:
        raise ValueError(f"{where}: 'fanout_cap' must be a positive integer")
    if fanout_cap > HARD_FANOUT_CEILING:
        raise ValueError(
            f"{where}: 'fanout_cap' {fanout_cap} exceeds the hard ceiling {HARD_FANOUT_CEILING}; "
            f"unbounded role spawn is refused")

    delivery = roles_block.get("delivery", "subprocess")
    if delivery not in ALLOWED_DELIVERY:
        raise ValueError(f"{where}: 'delivery' must be one of {sorted(ALLOWED_DELIVERY)}")

    ttl = roles_block.get("session_ttl_s", 900)
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl < 1:
        raise ValueError(f"{where}: 'session_ttl_s' must be a positive integer")

    cfg = RoleConfig(agents=agents, fanout_cap=fanout_cap, delivery=delivery, session_ttl_s=ttl)
    cfg.producer_role()  # validate producibility at load (fail closed)
    return cfg


def multi_agent_active(workflow: Any, env: Mapping[str, str] | None = None) -> bool:
    """True only when the workflow declares roles AND the activation flag is set.

    This is THE gate that keeps Phase 4 inert: default/v1 => False; a role-declaring workflow with
    the flag unset => False (single-executor path, byte-identical); only roles + flag => True.
    """
    env = env if env is not None else os.environ
    if not _flag_on(env.get(ACTIVATION_FLAG)):
        return False
    try:
        return resolve_roles(workflow) is not None
    except ValueError:
        # A malformed role block does not silently activate; surface it by NOT activating. (The
        # loud ValueError still fires when the runtime actually tries to build.)
        return False


def _flag_on(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}
