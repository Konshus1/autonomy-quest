"""The loop. This file is the product; everything else in the repo exists to keep it turning.

    observe -> decide -> act -> record -> learn -> (back to observe)

Two invariants are enforced here in code, not in prose, because prose does not stop a
running system from doing the wrong thing:

  1. A cycle is not complete until it has LEARNED. The learning row is written in the same
     transaction as the run's completion. There is no code path that finishes a run and
     skips the learning — that path is what would turn this into a cron job, and
     verify.sh gates on exactly the join it would break.

  2. The system NEVER grades its own homework. "Did the number move?" is answered by
     re-reading the mission's measure from its real source, not by asking the model how it
     thinks it did. A loop that scores itself optimises for feeling successful.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from datetime import date

from .budget import Budget, BudgetExceeded
from .config import Instance
from .db import Db
from .gateway import Gateway

log = logging.getLogger("aq.loop")


@dataclass
class Cycle:
    run_id: int
    work_id: int
    learned: str


def _total(*usages):
    """Sum the cost of every model call made during one cycle."""
    from .gateway import Usage
    return Usage(
        tokens_in=sum(u.tokens_in for u in usages),
        tokens_out=sum(u.tokens_out for u in usages),
        cost_usd=sum((u.cost_usd for u in usages), start=Decimal("0")),
    )


class Loop:
    def __init__(self, inst: Instance, db: Db, gw: Gateway) -> None:
        self.inst = inst
        self.db = db
        self.gw = gw
        self.budget = Budget(inst.budget, db)

    # -- one full turn ------------------------------------------------------
    def cycle(self) -> Cycle | None:
        """Run exactly one cycle. Returns None if there was nothing to do.

        Raises BudgetExceeded if the hard cap is hit. We do NOT catch that here and
        soldier on: a hard cap that can be argued past is not a cap, and 'just this once'
        is how an autonomous system quietly spends someone's month.
        """
        self.budget.check_hard_cap()

        world = self.observe()

        work, u_decide = self.decide(world)
        if work is None:
            log.info("nothing worth doing this cycle — the mission's number is where it should be")
            return None

        # Persist the decision BEFORE anything else happens to it. If we park it for a human,
        # or crash mid-act, there must still be a row saying what we decided and why. A
        # decision that exists only in memory is a decision nobody can audit.
        work.id = self.db.create_work(
            kind=work.kind, summary=work.summary, rationale=work.rationale,
            requires_human=work.requires_human,
        )

        # THE AUTONOMY GATE. Work above the instance's level does not execute; it parks and
        # the human is notified. This is the line the human drew in interview/01-mission.md,
        # and it is enforced BEFORE the act, not audited after it.
        if self.requires_human(work):
            self.db.park_for_human(work.id)
            self.notify_human(work)
            log.info("work #%s needs you — parked and notified, not executed", work.id)
            return None

        run_id = self.db.start_run(work.id)
        try:
            outcome, succeeded, u_act = self.act(work)
            insight, u_learn = self.learn(work, outcome, succeeded)
        except Exception as e:
            # Fail LOUD. A cycle that dies must leave a row saying it died. A silent
            # failure here is a system that looks alive and is not.
            self.db.fail_run(run_id, error=str(e))
            log.exception("cycle failed on work #%s — recorded, not swallowed", work.id)
            raise

        # EVERY model call in the cycle is charged to the run — the deciding and the
        # reflecting, not just the doing. Charging only the visible work would understate
        # the budget, and the budget is the one number the human is trusting us to keep
        # honest.
        usage = _total(u_decide, u_act, u_learn)

        # record + learn, ATOMICALLY. See invariant 1: there is no path that completes a
        # run and skips the learning, because they are the same commit.
        with self.db.tx() as tx:
            self.db.complete_run(tx, run_id, outcome=outcome, succeeded=succeeded, usage=usage)
            learning_id = self.db.write_learning(
                tx, run_id=run_id,
                insight=insight.insight, evidence=insight.evidence,
                scope=insight.scope, confidence=insight.confidence,
            )
            self.db.graph_link(tx, run_id=run_id, work_id=work.id, learning_id=learning_id)

        self.budget.check_soft_cap()
        log.info("cycle complete — run #%s, cost $%s, learned: %s",
                 run_id, usage.cost_usd, insight.insight)
        return Cycle(run_id=run_id, work_id=work.id, learned=insight.insight)

    # -- 1. observe ---------------------------------------------------------
    def observe(self) -> dict:
        """Read the world. GROUND TRUTH ONLY.

        The mission's measure is re-read from wherever it actually lives (the query, the
        table, the file the human named in the interview). We do not trust a cached number,
        and we do not ask the model how it thinks things are going. If the source is
        unreadable we say so and stop — a loop steering on a stale number is worse than a
        loop that halted, because it looks like it's working.
        """
        value = self.db.read_measure(self.inst.mission.measure)  # raises if unreadable
        self.db.record_measurement(self.inst.mission.measure, value)

        return {
            "mission": self.inst.mission,
            "now": value,
            "trend": self.db.measure_trend(self.inst.mission.measure, days=14),
            "open_work": self.db.open_work(),
            "recent_runs": self.db.recent_runs(limit=10),
            # Everything it has learned that hasn't been superseded. THIS is what makes
            # cycle 100 different from cycle 1.
            "learnings": self.db.live_learnings(limit=50),
            "parked": self.db.awaiting_human(),
            "spent_today": self.budget.spent_today(),
        }

    # -- 2. decide ----------------------------------------------------------
    def decide(self, world: dict):
        """Pick the highest-value thing to do RIGHT NOW.

        Not 'what's next in the queue'. What actually moves the mission's number, given
        everything learned so far. This is the reasoning tier — the one place we pay for
        judgment, because a bad decision here costs far more than the tokens saved by
        making it cheaply.
        """
        return self.gw.decide(tier="reasoning", world=world, template=self.inst.template)

    # -- 3. act -------------------------------------------------------------
    def act(self, work):
        """Do the thing. Working tier — most of the volume lives here."""
        return self.gw.execute(tier="working", work=work, boundaries=self.inst.mission.boundaries)

    # -- 4/5. learn ---------------------------------------------------------
    def learn(self, work, outcome: str, succeeded: bool):
        """What do we now believe that we didn't before, and what supports it?

        Note what is NOT asked: 'was that good?'. The model does not get to grade the run.
        It gets the outcome and the mission's real number, and it is asked what CHANGED in
        its beliefs. Failures are the richest input here — a run we had to roll back
        teaches more than three that worked, and it is the one signal the system must never
        need to be told twice.
        """
        return self.gw.reflect(
            tier="reasoning",
            work=work,
            outcome=outcome,
            succeeded=succeeded,
            prior=self.db.live_learnings(limit=50),
        )

    # -- the gate -----------------------------------------------------------
    def requires_human(self, work) -> bool:
        level = self.inst.budget.autonomy.level
        if level == "propose":
            return True
        if work.requires_human:                      # the template flagged it
            return True
        if level == "act-reversible":
            return not work.reversible               # anything hard to undo -> ask
        if level == "act-external":
            return work.spends_money or work.commits # money and promises still ask
        if level == "act-broad":
            return not self.inst.mission.within_boundaries(work)
        raise ValueError(f"unknown autonomy level: {level!r}")

    def notify_human(self, work) -> None:
        """Reach them where they said they'd be (interview/06-surfaces.md).

        If this fails we FAIL LOUD. A parked decision nobody was told about is a stalled
        loop that looks healthy, and it is the most common way one of these quietly dies.
        """
        self.inst.surfaces.notify(
            subject=f"[autonomy-quest] needs your decision: {work.summary}",
            body=work.rationale,
        )
