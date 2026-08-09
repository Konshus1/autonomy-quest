"""instance.yaml -> typed config. The instance's identity, loaded once.

Fails LOUD on a missing mission. An unaimed instance will run happily and accomplish
nothing, for weeks, before anyone notices — a system with no target never visibly misses.
So we refuse to construct one rather than defaulting the mission to something plausible.
"""

from __future__ import annotations

import os
import smtplib
from pathlib import Path
from dataclasses import dataclass, field
from email.message import EmailMessage

import yaml


class Unaimed(ValueError):
    """instance.yaml has no mission. The interview was skipped or left incomplete."""


@dataclass
class Measure:
    what: str
    where: str            # the QUERY or path the number really lives at. Ground truth.
    # A MEASURE WITHOUT A CEILING GETS RUN TO INFINITY.
    #
    # On a real box a mission of "60 models kept fresh" — measured as count(*) with no ceiling and
    # no DISTINCT — ran to 50,082. The loop was told to MOVE the number and it did, forever, because
    # nothing told it 60 was the target rather than the floor. Goodhart's law, live.
    #
    # target: the number that MEANS the mission is met. None = genuinely unbounded (rare; e.g.
    #         "maximize revenue"). If you set None, you are asserting more-is-always-better.
    # goal:   'reach_and_maintain' (default) — hit target, then HOLD it (re-verify, keep fresh),
    #         never grow past it. 'maximize' — no satisfied state; more is the point.
    target: float | None = None
    # A target can be a live QUERY instead of a frozen number. This matters for a
    # "maintain the whole catalog" mission: the catalog CHANGES, so a frozen target=320 would
    # report 'satisfied' even after 30 models are added and go stale — the measure drifting from
    # intent, the exact bug we just fixed, in miniature. target_query is re-read every cycle (like
    # `where`), so "satisfied" means "every in-scope model is fresh" and self-updates.
    target_query: str | None = None
    goal: str = "reach_and_maintain"   # reach_and_maintain | maximize

    def satisfied(self, current, target) -> bool:
        return (self.goal == "reach_and_maintain"
                and target is not None
                and current is not None
                and float(current) >= float(target))

    def overshooting(self, current, target, factor: float = 1.5) -> bool:
        """A reach_and_maintain measure that has blown WAY past its target is a measure being
        GAMED by volume, not a mission being served. This is the tripwire that did not fire."""
        return (self.goal == "reach_and_maintain"
                and target is not None
                and current is not None
                and float(current) > float(target) * factor)


@dataclass
class Boundaries:
    may_act_alone: list[str] = field(default_factory=list)
    must_ask_first: list[str] = field(default_factory=list)


@dataclass
class Mission:
    objective: str
    measure: Measure
    horizon: str
    boundaries: Boundaries

    def within_boundaries(self, work, *, per_plan_approval_usd=3.0,
                          blast_radius_gate_level=3) -> bool:
        """Consequence boundary: numeric expense + measured blast, never action category."""
        return not (work.expected_expense_usd > per_plan_approval_usd
                    or work.blast_radius_level >= blast_radius_gate_level)


@dataclass
class Autonomy:
    level: str = "act-external"
    per_plan_approval_usd: float = 3.0
    blast_radius_gate_level: int = 3
    ratchet: dict = field(default_factory=dict)
    expected_plan_cost_gate_usd: float = 3.0
    blast_radius_gate: float = 0.8


@dataclass
class Money:
    daily_soft_usd: float = 5.0
    monthly_hard_usd: float = 100.0


@dataclass
class BudgetCfg:
    money: Money = field(default_factory=Money)
    autonomy: Autonomy = field(default_factory=Autonomy)


@dataclass
class CuriosityBudget:
    cycles_per_day: int = 0
    max_cost_usd_per_day: float = 0.0


@dataclass
class CuriosityFrontier:
    # Ground-truth query returning the authoritative items curiosity may inspect.
    source: str = ""
    # Human-readable authority behind `source`: catalog, allowlist, registry, dataset, etc.
    authority: str = ""
    target: float | None = None
    target_query: str | None = None
    goal: str = "reach_and_maintain"
    max_items_per_cycle: int = 1


@dataclass
class CuriosityRatchet:
    window_cycles: int = 10
    shrink_factor: float = 0.5
    floor_cycles_per_day: int = 0


@dataclass
class Curiosity:
    enabled: bool = False
    budget: CuriosityBudget = field(default_factory=CuriosityBudget)
    frontier: CuriosityFrontier = field(default_factory=CuriosityFrontier)
    ratchet: CuriosityRatchet = field(default_factory=CuriosityRatchet)


@dataclass
class Engine:
    """Who executes the loop's thinking, and under what billing.

    bootstrap_agent is whoever ran the install and is long gone. resident_agent is what the loop
    drives on every cycle, forever. They are not the same thing and conflating them is how you
    ship a loop that depends on a human sitting there.
    """
    bootstrap_agent: str = ""
    resident_agent: str = "codex"
    mode: str = "subscription"          # subscription | api
    accounts: list = field(default_factory=list)
    installed_by_us: bool = False
    launch_flags: list = field(default_factory=list)


@dataclass
class Models:
    # subscription mode: the model is whatever the TUI agent runs — there is nothing to pick.
    mode: str = "api"                   # subscription | api
    engine: str = ""                    # codex | claude-code | copilot
    model: str = ""                     # what the agent reported it is actually running
    # api mode:
    provider: str = "openrouter"
    tiers: dict = field(default_factory=dict)
    monthly_estimate_usd: float = 0.0


@dataclass
class Surfaces:
    notify_channel: str = "email"
    notify_address: str = ""

    def notify(self, subject: str, body: str) -> None:
        """Reach the human. Fails LOUD — a parked decision nobody was told about is a
        stalled loop that looks healthy, and it's the most common way one of these dies."""
        if self.notify_channel == "none":
            return
        if self.notify_channel != "email":
            raise NotImplementedError(f"notify channel {self.notify_channel!r} not wired yet")
        if not self.notify_address:
            raise RuntimeError("surfaces.notify.address is empty — the system cannot reach you")

        host = os.environ.get("AQ_SMTP_HOST")
        if not host:
            raise RuntimeError("AQ_SMTP_HOST unset — cannot deliver. Not silently dropping this.")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.environ.get("AQ_SMTP_FROM", self.notify_address)
        msg["To"] = self.notify_address
        msg.set_content(body)
        with smtplib.SMTP_SSL(host, int(os.environ.get("AQ_SMTP_PORT", "465"))) as s:
            user = os.environ.get("AQ_SMTP_USER")
            if user:
                s.login(user, os.environ["AQ_SMTP_PASSWORD"])
            s.send_message(msg)


@dataclass(frozen=True)
class Workflow:
    name: str = "default"
    version: int = 1
    stages: tuple[str, ...] = ("observe", "decide", "act", "reflect", "learn")
    source: str = "workflows/default/v1/workflow.yaml"

    @property
    def identity(self) -> str:
        return f"{self.name}/v{self.version}"

    @classmethod
    def load(cls, selector: str = "default/v1") -> "Workflow":
        parts = selector.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1].startswith("v") or not parts[1][1:].isdigit():
            raise ValueError(f"invalid workflow selector {selector!r}; expected name/vN")
        root = Path(os.environ.get("AQ_WORKFLOWS_DIR", "workflows")).resolve()
        path = (root / parts[0] / parts[1] / "workflow.yaml").resolve()
        if root not in path.parents:
            raise ValueError("workflow selector escapes the workflow root")
        if not path.is_file():
            raise ValueError(f"workflow plugin not found: {path}")
        raw = yaml.safe_load(path.read_text()) or {}
        stages = tuple(stage.get("id") for stage in raw.get("stages") or [])
        required = ("observe", "decide", "act", "reflect", "learn")
        if raw.get("name") != parts[0] or int(raw.get("version", -1)) != int(parts[1][1:]):
            raise ValueError(f"workflow identity mismatch in {path}")
        if not raw.get("deterministic") or stages != required:
            raise ValueError(f"workflow {selector} must implement deterministic {required}")
        return cls(raw["name"], int(raw["version"]), stages, str(path))


@dataclass
class Instance:
    mission: Mission
    engine: Engine
    template: str
    datastore: dict
    models: Models
    budget: BudgetCfg
    surfaces: Surfaces
    curiosity: Curiosity = field(default_factory=Curiosity)
    workflow: Workflow = field(default_factory=Workflow)

    @classmethod
    def load(cls, path: str = "instance.yaml") -> "Instance":
        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}

        m = raw.get("mission") or {}
        objective = (m.get("objective") or "").strip()
        measure = m.get("measure") or {}
        if not objective or not (measure.get("what") or "").strip():
            raise Unaimed(
                "instance.yaml has no mission. An unaimed instance accomplishes nothing. "
                "Go back to interview/01-mission.md and answer it."
            )

        b = (raw.get("budget") or {})
        s = (raw.get("surfaces") or {}).get("notify", {})
        c = raw.get("curiosity") or {}

        eng = raw.get("engine") or {}
        return cls(
            engine=Engine(
                bootstrap_agent=eng.get("bootstrap_agent", ""),
                resident_agent=eng.get("resident_agent", "codex"),
                mode=eng.get("mode", "subscription"),
                accounts=eng.get("accounts") or [],
                installed_by_us=bool(eng.get("installed_by_us", False)),
                launch_flags=eng.get("launch_flags") or [],
            ),
            mission=Mission(
                objective=objective,
                measure=Measure(
                    what=measure["what"], where=measure["where"],
                    target=measure.get("target"),
                    target_query=measure.get("target_query"),
                    goal=measure.get("goal", "reach_and_maintain"),
                ),
                horizon=m.get("horizon", ""),
                boundaries=Boundaries(**(m.get("boundaries") or {})),
            ),
            template=raw.get("template", "running-a-business"),
            datastore=raw.get("datastore") or {"engine": "postgres", "graph": "age"},
            models=Models(**(raw.get("models") or {})),
            budget=BudgetCfg(
                money=Money(**(b.get("money") or {})),
                autonomy=Autonomy(**(b.get("autonomy") or {})),
            ),
            surfaces=Surfaces(
                notify_channel=s.get("channel", "email"),
                notify_address=s.get("address", ""),
            ),
            curiosity=Curiosity(
                enabled=bool(c.get("enabled", False)),
                budget=CuriosityBudget(**(c.get("budget") or {})),
                frontier=CuriosityFrontier(**(c.get("frontier") or {})),
                ratchet=CuriosityRatchet(**(c.get("ratchet") or {})),
            ),
            workflow=Workflow.load(raw.get("workflow", "default/v1")),
        )
