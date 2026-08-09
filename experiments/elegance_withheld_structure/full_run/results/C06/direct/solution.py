import datetime
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ProposalResult:
    accepted: bool
    reason: str
    status: str
    notices: tuple
    timestamp: datetime.datetime | None


class Exhibit:
    _ACTIONS = ("reserve", "install", "open", "close", "maintain", "retire")
    _TRANSITIONS = {
        ("draft", "reserve"): "reserved",
        ("reserved", "install"): "installed",
        ("installed", "open"): "open",
        ("open", "close"): "closed",
        ("closed", "open"): "open",
        ("closed", "maintain"): "maintenance",
        ("maintenance", "open"): "open",
    }

    def __init__(self, exhibit_id, clock: Callable[[], datetime.datetime]):
        self.exhibit_id = exhibit_id
        self._clock = clock
        self.status = "draft"
        self.history = ()

    def propose(self, action):
        if action not in self._ACTIONS:
            raise ValueError(f"unknown action: {action!r}")

        if action == "retire" and self.status != "retired":
            new_status = "retired"
        else:
            new_status = self._TRANSITIONS.get((self.status, action))

        if new_status is None:
            return ProposalResult(
                accepted=False,
                reason=f"INVALID_FROM_{self.status.upper()}",
                status=self.status,
                notices=(),
                timestamp=None,
            )

        timestamp = self._clock()
        result = ProposalResult(
            accepted=True,
            reason="ACCEPTED",
            status=new_status,
            notices=(f"{action.upper()}:{self.exhibit_id}",),
            timestamp=timestamp,
        )
        self.status = new_status
        self.history = self.history + (result,)
        return result
