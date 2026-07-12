"""When to keep going, when to ask, and when to stop.

Adapted from a hard-won pattern in the reference implementation (BB decision #29), which learned
these rules the expensive way. Two ideas, both earned:

1. ARTIFACT-OR-ESCALATE.
   Every cycle must produce a real artifact, a real change, or a genuine escalation. "I analysed
   the situation and here are my next steps" is NOT a cycle — it is a machine describing work
   instead of doing it, and it will do that forever, at cost, while looking busy. A loop that can
   narrate its way through a cycle will.

2. GRADUATED ESCALATION, with a floor.
   A stuck loop must not hammer the same wall forever, and it must not wake a human on the first
   stumble. So it climbs:

       autonomous  ->  self-nudge (try a different angle)
                   ->  ask a peer agent
                   ->  ask the HUMAN (email — expensive, use it properly)
                   ->  HIBERNATE (stop. stop spending.)

   The hibernate floor is the one people leave out, and it is the one that saves the money. An
   autonomous system that is stuck and never stops is a system that bills you all night to
   accomplish nothing.

The counter is over CONSECUTIVE UNPRODUCTIVE CYCLES — cycles that produced no artifact and moved
no number. A cycle that did real work resets it to zero, because that loop is not stuck; it is
working.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

log = logging.getLogger("aq.escalation")


# The ladder. Tuned so a human is woken only after the system has genuinely tried.
SELF_NUDGE_AT = 3
PEER_AT       = 5
HUMAN_AT      = 8
HIBERNATE_AT  = 12


class Hibernate(RuntimeError):
    """Stuck, and every avenue exhausted. The loop STOPS.

    This is a correct outcome, not a crash. A system that is stuck and keeps spending is worse
    than one that stops and says so.
    """


@dataclass
class Verdict:
    level: str           # autonomous | self-nudge | peer | human | hibernate
    unproductive: int
    guidance: str        # injected into the next decide() prompt
    notify_human: bool = False


class Escalation:
    def __init__(self, db) -> None:
        self.db = db

    # -- artifact-or-escalate -------------------------------------------------
    @staticmethod
    def was_productive(outcome: str, succeeded: bool, evidence: str,
                       measure_before: Decimal, measure_after: Decimal) -> bool:
        """Did this cycle actually DO something?

        Deliberately strict, and deliberately NOT a judgement call handed back to the model — we
        do not ask the agent whether it was productive, because it will say yes. We check:

          * the mission's number moved, OR
          * it points at real evidence AND claims success.

        A failed cycle is NOT unproductive if it produced a real learning — failing informatively
        is progress. But a cycle that succeeded, moved nothing, and can point at nothing is a
        cycle that narrated. Those are the ones that burn a night.
        """
        if measure_after != measure_before:
            return True                                   # the number moved. That is the job.
        if succeeded and evidence and evidence.strip() and "n/a" not in evidence.lower():
            return True                                   # real artifact, pointable
        if not succeeded:
            # A genuine failure teaches. It counts as productive ONCE — but consecutive failures
            # still climb the ladder, because a loop failing the same way repeatedly IS stuck.
            return False
        return False

    # -- the ladder -----------------------------------------------------------
    def assess(self, unproductive: int) -> Verdict:
        if unproductive >= HIBERNATE_AT:
            raise Hibernate(
                f"{unproductive} consecutive cycles produced nothing. Every avenue has been tried: "
                f"different angles, a peer, and the human. The loop is STOPPING rather than "
                f"continuing to spend. Look at the parked work and the recent failures — something "
                f"upstream is wrong, and more cycles will not fix it."
            )

        if unproductive >= HUMAN_AT:
            return Verdict(
                level="human", unproductive=unproductive, notify_human=True,
                guidance=(
                    "You have produced nothing for several cycles and a peer could not unstick you. "
                    "The human is being asked. Do NOT retry the same approach while waiting. If "
                    "there is genuinely nothing else worth doing, say do_nothing honestly."),
            )

        if unproductive >= PEER_AT:
            return Verdict(
                level="peer", unproductive=unproductive,
                guidance=(
                    "You are stuck. Ask another agent: use bb_handoff to describe precisely what "
                    "you tried and what blocked you. Then pick DIFFERENT work — do not sit and "
                    "wait, and do not retry what already failed."),
            )

        if unproductive >= SELF_NUDGE_AT:
            return Verdict(
                level="self-nudge", unproductive=unproductive,
                guidance=(
                    f"WARNING: {unproductive} cycles in a row produced no artifact and moved no "
                    f"number. Whatever you have been trying is not working. Do NOT try it again "
                    f"with more determination. Pick a different angle, or a smaller piece, or "
                    f"work out what is actually blocking you and fix THAT. Read your own recent "
                    f"failures before deciding."),
            )

        return Verdict(level="autonomous", unproductive=unproductive, guidance="")

    # -- state ---------------------------------------------------------------
    def unproductive_streak(self) -> int:
        """Consecutive unproductive cycles, from the DB. Not a counter in memory — a counter that
        resets when the process restarts would let a stuck loop hammer forever by crashing."""
        rows = self.db.recent_productivity(limit=HIBERNATE_AT + 2)
        streak = 0
        for r in rows:                                    # newest first
            if r["productive"]:
                break
            streak += 1
        return streak
