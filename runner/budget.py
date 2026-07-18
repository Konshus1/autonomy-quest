"""Money. Two caps, and one of them is not negotiable.

The system runs continuously, which means it spends continuously. A cap that can be argued
past is not a cap — "just this once" is precisely how an autonomous operator quietly spends
someone's month. So the hard cap raises and the loop stops. There is no override flag here,
deliberately: if the human wants to spend more, they raise the cap in instance.yaml, which
is an act they perform knowingly rather than one the system performs on their behalf.

Spend is computed from the RUNS TABLE — the actual recorded cost of actual completed calls —
not from a running counter held in memory. A counter resets when the process restarts, and a
budget you can reset by crashing is not a budget.
"""

from __future__ import annotations

import logging
from decimal import Decimal

log = logging.getLogger("aq.budget")


class BudgetExceeded(RuntimeError):
    """Hard cap hit. The loop stops. This is a correct outcome, not a bug."""


class Budget:
    def __init__(self, cfg, db) -> None:
        self.daily_soft = Decimal(str(cfg.money.daily_soft_usd))
        self.monthly_hard = Decimal(str(cfg.money.monthly_hard_usd))
        self.db = db

    def spent_today(self) -> Decimal:
        return self.db.sum_cost(since="today")

    def spent_this_month(self) -> Decimal:
        return self.db.sum_cost(since="month")

    @property
    def metered(self) -> bool:
        """Is there a dollar cap to enforce at all?

        In SUBSCRIPTION mode the loop drives a TUI agent under a flat-rate plan: there is no
        per-cycle dollar cost, so the caps are 0 — meaning "not applicable", NOT "you may
        spend nothing".

        Getting this wrong is not academic. The naive check (`spent >= monthly_hard`) reads
        0 >= 0 as CAP EXCEEDED and halts the loop before its very first cycle — a system that
        can never turn, for a reason that looks like a budget problem and isn't. Found by an
        agent reading this code during a real bootstrap, which is exactly the kind of bug that
        survives any number of readings by its author.
        """
        return self.monthly_hard > 0

    def check_hard_cap(self) -> None:
        """Called at the top of every cycle, BEFORE any spending. Ground truth, every time."""
        if not self.metered:
            return  # subscription mode: the constraint is rate limits, not dollars
        spent = self.spent_this_month()
        if spent >= self.monthly_hard:
            raise BudgetExceeded(
                f"Monthly hard cap reached: ${spent} of ${self.monthly_hard}. "
                f"The loop has stopped and will not spend further. "
                f"Raise budget.money.monthly_hard_usd in instance.yaml to continue."
            )

    def check_soft_cap(self) -> bool:
        """Soft cap slows the cadence and tells the human. It does not stop the work.

        Returns True if we are over. The caller throttles; it does not halt.
        """
        spent = self.spent_today()
        if self.soft_cap_reached(spent):
            log.warning(
                "daily soft cap reached: $%s of $%s — slowing cadence. "
                "Not stopping; the monthly hard cap is what stops the loop.",
                spent, self.daily_soft,
            )
            return True
        return False

    def soft_cap_reached(self, spent: Decimal | None = None) -> bool:
        """Predicate form of the soft cap for optional work.

        Mission work may continue past the soft cap, but optional drives should stop first.
        """
        if not self.metered or self.daily_soft <= 0:
            return False
        return (self.spent_today() if spent is None else spent) >= self.daily_soft

    def would_exceed(self, estimate: Decimal) -> bool:
        """Check BEFORE an expensive call, not after it.

        Discovering you blew the cap by reading the receipt is not enforcement.
        """
        return (self.spent_this_month() + estimate) >= self.monthly_hard
