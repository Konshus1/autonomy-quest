"""The loop. This file is the product; everything else in the repo exists to keep it turning.

    observe -> decide -> act -> record -> learn -> (back to observe)

It runs through an EXECUTOR, which is either the human's TUI agent under their subscription
(flat rate, search included) or a metered model API. The loop does not know or care which.

Three invariants are enforced here in code, not in prose, because prose does not stop a running
system from doing the wrong thing:

  1. A cycle is not complete until it has LEARNED. The learning row is written in the same
     transaction as the run's completion. There is no code path that finishes a run and skips
     the learning — that path is what would turn this into a cron job, and verify.sh gates on
     exactly the join it would break.

  2. The system NEVER grades its own homework. "Did the number move?" is answered by re-reading
     the mission's measure from its real source, not by asking the model how it thinks it did.
     A loop that scores itself optimises for feeling successful.

  3. The autonomy gate fires BEFORE the act, not as an audit after it. A boundary you check
     afterwards is not a boundary; it's a regret.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from . import prompts
from .budget import Budget, BudgetExceeded
from .config import Instance
from .db import Db, Work
from .escalation import Escalation, Hibernate
from .executor import AgentFailed, RateLimited, Usage

log = logging.getLogger("aq.loop")


@dataclass
class Cycle:
    run_id: int
    work_id: int
    learned: str


def _total(*usages) -> Usage:
    """Sum the cost of every model call made during one cycle."""
    return Usage(
        tokens_in=sum(u.tokens_in for u in usages),
        tokens_out=sum(u.tokens_out for u in usages),
        cost_usd=sum((u.cost_usd for u in usages), start=Decimal("0")),
    )


class Loop:
    def __init__(self, inst: Instance, db: Db, executor) -> None:
        self.inst = inst
        self.db = db
        self.ex = executor
        self.budget = Budget(inst.budget, db)
        self.esc = Escalation(db)

    # -- one full turn ------------------------------------------------------
    def cycle(self) -> Cycle | None:
        """Run exactly one cycle. Returns None if there was nothing to do.

        Raises BudgetExceeded if the hard cap is hit; we do NOT catch it and soldier on. A hard
        cap that can be argued past is not a cap, and "just this once" is how an autonomous
        system quietly spends someone's month.
        """
        self.budget.check_hard_cap()

        world = self.observe()
        measure_before = world["now"]

        # THE LADDER. If recent cycles produced nothing, say so LOUDLY in the prompt — a stuck
        # loop that is not told it is stuck will try the same thing again with more determination.
        verdict = self.esc.assess(self.esc.unproductive_streak())   # raises Hibernate at the floor
        if verdict.level != "autonomous":
            log.warning("escalation: %s (%d unproductive cycles)", verdict.level, verdict.unproductive)
        if verdict.notify_human:
            self.notify_human_stuck(verdict, world)

        decision, u_decide = self.ex.run(
            prompts.decide(world, self.inst.template, guidance=verdict.guidance),
            prompts.DECIDE_SCHEMA, tier="reasoning"
        )
        if decision.get("do_nothing"):
            log.info("nothing worth doing this cycle — not inventing busywork")
            return None

        work = Work(
            id=0,
            kind=decision["kind"],
            summary=decision["summary"],
            rationale=decision["rationale"],
            reversible=decision["reversible"],
            spends_money=decision["spends_money"],
            touches_human=decision["touches_human"],
            commits=decision["commits"],
        )

        # Persist the decision BEFORE anything happens to it. If we park it or crash mid-act,
        # there must still be a row saying what we decided and why. A decision that exists only
        # in memory is a decision nobody can audit.
        work.id = self.db.create_work(
            kind=work.kind, summary=work.summary, rationale=work.rationale,
            requires_human=work.requires_human,
        )

        # INVARIANT 3 — the gate fires BEFORE the act.
        if self.requires_human(work):
            self.db.park_for_human(work.id)
            self.notify_human(work)
            log.info("work #%s needs you — parked and notified, NOT executed", work.id)
            return None

        run_id = self.db.start_run(work.id)
        try:
            result, u_act = self.ex.run(
                prompts.act(work, self.inst.mission.boundaries), prompts.ACT_SCHEMA, tier="working"
            )
            outcome, succeeded = result["outcome"], result["succeeded"]

            insight, u_learn = self.ex.run(
                prompts.reflect(work, outcome, succeeded, self.db.live_learnings(limit=50)),
                prompts.REFLECT_SCHEMA, tier="reasoning",
            )
        except RateLimited:
            # Expected on a subscription — the plan is simply used up for now. This is NOT a
            # failed run: nothing went wrong, we just have to wait. Roll the run back so the
            # work is picked up again rather than being recorded as a failure it wasn't.
            self.db.abandon_run(run_id)
            raise
        except Exception as e:
            # Fail LOUD. A cycle that dies leaves a row saying it died. A silent failure here is
            # a system that looks alive and is not.
            self.db.fail_run(run_id, error=str(e))
            log.exception("cycle failed on work #%s — recorded, not swallowed", work.id)
            raise

        # EVERY call in the cycle is charged to the run — the deciding and the reflecting, not
        # just the doing. Charging only the visible work understates the budget, and the budget
        # is the one number the human is trusting us to keep honest. (On subscription mode these
        # are all zero, which is the point.)
        usage = _total(u_decide, u_act, u_learn)

        # ARTIFACT-OR-ESCALATE. Did this cycle actually DO something, or did it narrate?
        # We do NOT ask the agent whether it was productive — it would say yes. We re-read the
        # mission's number and check for pointable evidence.
        measure_after = self.db.read_measure(self.inst.mission.measure)
        productive = self.esc.was_productive(
            outcome, succeeded, result.get("evidence", ""), measure_before, measure_after)
        if not productive:
            log.warning("cycle produced no artifact and moved no number — this counts toward escalation")

        # INVARIANT 1 — record and learn are ONE transaction. There is no path that completes a
        # run and skips the learning, because they are the same commit.
        with self.db.tx() as tx:
            self.db.complete_run(
                tx, run_id, outcome=outcome, succeeded=succeeded, usage=usage,
                productive=productive, evidence=result.get("evidence", ""),
                measure_before=measure_before, measure_after=measure_after,
                escalation_level=verdict.level)
            learning_id = self.db.write_learning(
                tx, run_id=run_id,
                insight=insight["insight"], evidence=insight["evidence"],
                scope=insight["scope"], confidence=insight["confidence"],
            )
            self.db.graph_link(tx, run_id=run_id, work_id=work.id, learning_id=learning_id)

        self.db.beat("turning", f"run #{run_id} complete")
        self.budget.check_soft_cap()
        log.info("cycle complete — run #%s, cost $%s, learned: %s",
                 run_id, usage.cost_usd, insight["insight"][:80])
        return Cycle(run_id=run_id, work_id=work.id, learned=insight["insight"])

    # -- run forever --------------------------------------------------------
    def forever(self, interval_s: int = 300) -> None:
        """The system's actual life. Turn, wait, turn again.

        A rate limit is not a crash — it's a subscription doing its job. We wait it out and pick
        up exactly where we left off.
        """
        # A restart must NOT wash away an unresolved hibernation. If we come back up stuck, we
        # go straight back to waiting — without re-emailing, and without spending a cycle to
        # rediscover that we are stuck.
        open_h = self.db.open_hibernation()
        if open_h is not None:
            log.error("came up with an UNRESOLVED hibernation (#%s) — waiting for a real signal, "
                      "not spending.", open_h["id"])
            self.enter_hibernation(Hibernate(open_h["reason"]))

        while True:
            try:
                self.cycle()
            except RateLimited as e:
                wait = e.retry_after_s or 900
                # DEADLINE, DB-computed. After it passes, "rate limited" stops being an excuse.
                self.db.beat("rate_limited", f"plan exhausted; waiting {wait}s", retry_after_s=wait)
                log.warning("rate limited — sleeping %ss. The loop is fine; the plan is busy.", wait)
                time.sleep(wait)
                continue
            except Hibernate as e:
                # Stuck, everything tried. STOP spending — but DO NOT vanish.
                #
                # The bug this replaces: we exited, systemd restarted us (Restart=always), we
                # re-read the same streak, hibernated again and EMAILED AGAIN — forever. Cost
                # control became a notify storm, and nothing recorded that a human was needed,
                # so their reply had nothing to resume.
                self.enter_hibernation(e)
                return
            except BudgetExceeded as e:
                log.error("HARD CAP REACHED — the loop has STOPPED. %s", e)
                self.inst.surfaces.notify(
                    subject="[autonomy-quest] budget cap reached — the loop has stopped",
                    body=str(e),
                )
                return
            except AgentFailed as e:
                log.error("agent failed this cycle (recorded): %s", e)
            except Exception:
                log.exception("cycle blew up (recorded). Continuing — one bad cycle is not a dead loop.")
            time.sleep(interval_s)

    # -- 1. observe ---------------------------------------------------------
    def observe(self) -> dict:
        """Read the world. GROUND TRUTH ONLY.

        The mission's measure is re-read from wherever it actually lives — the query the human
        gave us in the interview. We do not trust a cached number, and we never ask the model how
        it thinks things are going. If the source is unreadable we stop: a loop steering on a
        number it did not actually read is worse than a halted loop, because it looks like it's
        working.
        """
        value = self.db.read_measure(self.inst.mission.measure)   # raises if unreadable
        self.db.record_measurement(self.inst.mission.measure, value)

        return {
            "mission": self.inst.mission,
            "now": value,
            "trend": self.db.measure_trend(self.inst.mission.measure, days=14),
            "open_work": self.db.open_work(),
            "recent_runs": self.db.recent_runs(limit=10),
            # Everything it believes that hasn't been superseded. THIS is what makes cycle 100
            # different from cycle 1.
            "learnings": self.db.live_learnings(limit=50),
            "parked": self.db.awaiting_human(),
            "spent_today": self.budget.spent_today(),
        }

    # -- the gate -----------------------------------------------------------
    def requires_human(self, work: Work) -> bool:
        level = self.inst.budget.autonomy.level
        if level == "propose":
            return True
        if work.requires_human:
            return True
        if level == "act-reversible":
            return not work.reversible or work.spends_money or work.touches_human or work.commits
        if level == "act-external":
            return work.spends_money or work.commits
        if level == "act-broad":
            return not self.inst.mission.within_boundaries(work)
        raise ValueError(f"unknown autonomy level: {level!r}")

    # -- hibernation ---------------------------------------------------------
    def enter_hibernation(self, e: Hibernate) -> None:
        """Record it, tell the human ONCE, and WAIT for a real signal.

        We do not exit the process. Exiting hands control to the supervisor, which will restart
        us into the same wall. We sit here, cheaply, spending nothing, until a human approves
        something or a peer sends a message.
        """
        open_h = self.db.open_hibernation()
        if open_h is None:
            hid = self.db.hibernate(str(e), self.esc.unproductive_streak())
            open_h = self.db.open_hibernation()
            log.error("HIBERNATING (#%s). %s", hid, e)
        else:
            log.error("still hibernating (#%s) — NOT re-notifying.", open_h["id"])

        if open_h["notified_at"] is None:
            self.inst.surfaces.notify(
                subject=f"[autonomy-quest] {self.inst.engine.resident_agent} instance HIBERNATED — it is stuck and has stopped",
                body=(f"{e}\n\nMISSION: {self.inst.mission.objective}\n\n"
                      f"It has STOPPED SPENDING. It will resume the moment you approve something "
                      f"in the UI, or an agent sends it a message — and not before. Elapsed time "
                      f"will not wake it, and neither will a reboot."))
            self.db.mark_hibernation_notified(open_h["id"])

        # Wait for a REAL signal. Not a timer.
        #
        # NON-BUSY and SUPERVISOR-COMPATIBLE: we sleep, we make ZERO model calls, and we emit a
        # cheap heartbeat so a watchdog can tell "correctly waiting" from "dead" — WITHOUT it
        # counting as progress. A hibernating loop that looks dead gets killed and restarted into
        # the same wall; a hibernating loop that looks productive resets the ladder. It must look
        # like exactly what it is: alive, stopped, and waiting for you.
        while True:
            self.db.beat("hibernating", f"waiting on a human or peer signal (#{open_h['id']})")
            sig = self.db.resume_signal(open_h["hibernated_at"])
            if sig:
                self.db.resume_hibernation(open_h["id"], sig)
                log.warning("RESUMING — %s", sig)
                self.db.beat("turning", f"resumed: {sig[:80]}")
                return
            time.sleep(30)

    def notify_human_stuck(self, verdict, world) -> None:
        """The loop is stuck enough to be worth a human's attention. Say what was tried."""
        recent = "\n".join(f"- {r['summary']}: {r['outcome'][:120]}" for r in world["recent_runs"][:5])
        self.inst.surfaces.notify(
            subject=f"[autonomy-quest] stuck — {verdict.unproductive} cycles produced nothing",
            body=(f"The loop has produced no artifact and moved no number for "
                  f"{verdict.unproductive} consecutive cycles.\n\n"
                  f"MISSION: {self.inst.mission.objective}\n"
                  f"MEASURE: {self.inst.mission.measure.what} = {world['now']}\n\n"
                  f"WHAT IT TRIED:\n{recent}\n\n"
                  f"It will keep trying different angles, and will HIBERNATE (stop spending) "
                  f"at {12} consecutive unproductive cycles rather than burn your budget on a wall."))

    def notify_human(self, work: Work) -> None:
        """Reach them where they said they'd be (interview/06-surfaces.md).

        If this fails we FAIL LOUD. A parked decision nobody was told about is a stalled loop that
        looks healthy — the most common way one of these quietly dies.
        """
        self.inst.surfaces.notify(
            subject=f"[autonomy-quest] needs your decision: {work.summary}",
            body=f"{work.rationale}\n\nApprove it and the loop will pick it up on the next cycle.",
        )
