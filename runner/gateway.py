"""The model gateway. Every model call in the system goes through here.

Why one chokepoint: cost accounting has to be unavoidable. If any part of the loop could
call a model directly, the budget would be an estimate rather than a fact. Usage comes back
from the provider's own token counts and is written to the run row — the budget is then
summed from those rows, so it survives a crash and can't be reset by restarting.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import httpx

from .db import Work

log = logging.getLogger("aq.gateway")

_DATA = Path(__file__).resolve().parent.parent / "data" / "models.json"


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal("0")


@dataclass
class Insight:
    insight: str
    evidence: str
    scope: str = "local"
    confidence: float = 0.5


class Gateway:
    def __init__(self, models, timeout: float = 120.0) -> None:
        self.models = models
        self.prices = {m["id"]: m for m in json.loads(_DATA.read_text())["models"]}
        self.provider = models.provider
        self.timeout = timeout

        if self.provider == "openrouter":
            self.base = "https://openrouter.ai/api/v1/chat/completions"
            self.key = os.environ.get("OPENROUTER_API_KEY")
        elif self.provider == "anthropic":
            self.base = "https://api.anthropic.com/v1/messages"
            self.key = os.environ.get("ANTHROPIC_API_KEY")
        else:
            raise ValueError(f"provider {self.provider!r} not wired")

        if not self.key:
            raise RuntimeError(
                f"no API key for provider {self.provider!r}. Put it in .env — "
                f"the loop cannot turn without a model."
            )

    # -- the one place a model is called -------------------------------------
    def _call(self, tier: str, system: str, user: str) -> tuple[str, Usage]:
        model = self.models.tiers[tier]
        if self.provider == "anthropic":
            r = httpx.post(
                self.base, timeout=self.timeout,
                headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                json={"model": model, "max_tokens": 2000, "system": system,
                      "messages": [{"role": "user", "content": user}]},
            )
            r.raise_for_status()
            d = r.json()
            text = d["content"][0]["text"]
            u = d["usage"]
            tin, tout = u["input_tokens"], u["output_tokens"]
        else:
            r = httpx.post(
                self.base, timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.key}"},
                json={"model": model, "max_tokens": 2000, "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}]},
            )
            r.raise_for_status()
            d = r.json()
            text = d["choices"][0]["message"]["content"]
            u = d.get("usage", {})
            tin, tout = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)

        return text, Usage(tokens_in=tin, tokens_out=tout, cost_usd=self._cost(model, tin, tout))

    def _cost(self, model: str, tin: int, tout: int) -> Decimal:
        p = self.prices.get(model)
        if not p or not p.get("price_in"):
            # We do NOT invent a price. An unknown-cost call is recorded at zero and said
            # so, loudly — a fabricated price would silently corrupt the budget, which is
            # the one number the human is trusting us to keep honest.
            log.warning("no verified price for %s — cost recorded as 0. Budget is UNDERSTATED. "
                        "Add its price to data/models.json.", model)
            return Decimal("0")
        return (Decimal(str(p["price_in"])) * tin + Decimal(str(p["price_out"])) * tout) / Decimal(1_000_000)

    @staticmethod
    def _json(text: str) -> dict:
        """Parse the model's JSON reply.

        Models sometimes return a LIST of objects where one was asked for (reflect, in
        particular, likes to volunteer several insights). We take the first rather than
        crashing — but we do NOT silently merge or invent fields. If the object is missing a
        required key, the caller raises and the run is recorded as failed, because a cycle
        that quietly filled in a blank is a cycle whose learning is fiction.
        """
        s = text.strip()
        if s.startswith("```"):
            s = s.split("```")[1].removeprefix("json").strip()
        d = json.loads(s)
        if isinstance(d, list):
            if not d:
                raise ValueError("model returned an empty JSON list where an object was required")
            log.warning("model returned %d objects where 1 was asked for — taking the first", len(d))
            d = d[0]
        if not isinstance(d, dict):
            raise ValueError(f"model returned {type(d).__name__}, expected a JSON object")
        return d

    # -- decide --------------------------------------------------------------
    def decide(self, tier: str, world: dict, template: str) -> tuple[Work | None, Usage]:
        mission = world["mission"]
        learned = "\n".join(f"- {l['insight']}" for l in world["learnings"]) or "(nothing yet — first cycles)"
        recent = "\n".join(f"- {r['summary']}: {r['outcome']}" for r in world["recent_runs"]) or "(no runs yet)"

        text, usage = self._call(
            tier,
            system=(
                "You run an autonomous operations loop aimed at one mission. Pick the single "
                "highest-value thing to do RIGHT NOW — the thing that most moves the mission's "
                "number, not the next item in a queue. Use what has been learned; that is the "
                "point of having learned it. If nothing is worth doing, say so honestly rather "
                "than inventing busywork. Reply with JSON only."
            ),
            user=f"""MISSION: {mission.objective}
MEASURE: {mission.measure.what} — currently {world['now']}
HORIZON: {mission.horizon}
MAY ACT ALONE: {mission.boundaries.may_act_alone}
MUST ASK FIRST: {mission.boundaries.must_ask_first}
TEMPLATE: {template}

WHAT I HAVE LEARNED SO FAR:
{learned}

RECENT RUNS:
{recent}

Reply with JSON:
{{"do_nothing": false, "kind": "...", "summary": "...", "rationale": "why THIS moves the number",
  "reversible": true, "spends_money": false, "touches_human": false, "commits": false}}""",
        )
        d = self._json(text)
        if d.get("do_nothing"):
            return None, usage
        return Work(
            id=0,  # not persisted yet — loop.py writes the row and fills this in
            kind=d["kind"], summary=d["summary"], rationale=d["rationale"],
            reversible=d.get("reversible", True),
            spends_money=d.get("spends_money", False),
            touches_human=d.get("touches_human", False),
            commits=d.get("commits", False),
        ), usage

    # -- act -----------------------------------------------------------------
    def execute(self, tier: str, work: Work, boundaries) -> tuple[str, bool, Usage]:
        text, usage = self._call(
            tier,
            system=(
                "Do the work. Report what you ACTUALLY did in plain language, including what "
                "failed. Do not report success you did not achieve — a false success is worse "
                "than a failure, because it poisons everything the system learns from it. "
                "Reply with JSON only."
            ),
            user=f"""WORK: {work.summary}
WHY: {work.rationale}
YOU MAY: {boundaries.may_act_alone}
YOU MAY NOT (without asking): {boundaries.must_ask_first}

Reply with JSON: {{"outcome": "what actually happened", "succeeded": true}}""",
        )
        d = self._json(text)
        return d["outcome"], bool(d["succeeded"]), usage

    # -- learn ---------------------------------------------------------------
    def reflect(self, tier: str, work: Work, outcome: str, succeeded: bool, prior) -> tuple[Insight, Usage]:
        known = "\n".join(f"- {l['insight']}" for l in prior) or "(nothing yet)"
        text, usage = self._call(
            tier,
            system=(
                "What do you now believe that you did not believe before? Be specific and "
                "falsifiable. A failure teaches more than a success — mine it. Say 'generalisable' "
                "ONLY if this would hold for a different instance with a different mission (a fact "
                "about how autonomous operation works, not a fact about this business). Most "
                "learnings are local; that is fine and expected. Do not restate what is already "
                "known. Reply with JSON only."
            ),
            user=f"""WORK: {work.summary}
OUTCOME: {outcome}
SUCCEEDED: {succeeded}

ALREADY KNOWN (do not repeat):
{known}

Reply with JSON:
{{"insight": "...", "evidence": "what in the outcome supports this",
  "scope": "local|generalisable", "confidence": 0.0-1.0}}""",
        )
        d = self._json(text)
        return Insight(
            insight=d["insight"], evidence=d["evidence"],
            scope=d.get("scope", "local"), confidence=float(d.get("confidence", 0.5)),
        ), usage
