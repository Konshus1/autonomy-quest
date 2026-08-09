"""Order-independent access policy evaluation."""

from copy import deepcopy
from datetime import datetime, time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class AccessService:
    """Evaluate access requests against a fixed collection of requirements."""

    _FAILURE_MESSAGES = {
        "badge": "badge not allowed",
        "escort": "escort required",
        "window": "outside access window",
        "closure": "temporarily closed",
    }

    def __init__(self, requirements: Iterable[Mapping[str, Any]]):
        self._requirements = tuple(deepcopy(list(requirements)))

    @staticmethod
    def _clock(value: str) -> time:
        return datetime.strptime(value, "%H:%M").time()

    @classmethod
    def _inside_window(
        cls, requirement: Mapping[str, Any], timestamp: datetime
    ) -> bool:
        weekdays = set(requirement["weekdays"])
        start = cls._clock(requirement["start"])
        end = cls._clock(requirement["end"])
        current = timestamp.time()

        if start == end:
            return timestamp.weekday() in weekdays

        if start < end:
            return timestamp.weekday() in weekdays and start <= current < end

        if current >= start:
            return timestamp.weekday() in weekdays

        if current < end:
            previous_weekday = (timestamp.weekday() - 1) % 7
            return previous_weekday in weekdays

        return False

    @classmethod
    def _failure(
        cls, requirement: Mapping[str, Any], request: Mapping[str, Any]
    ) -> Optional[str]:
        kind = requirement["type"]

        if kind == "badge":
            failed = request["badge"] not in requirement["allowed"]

        elif kind == "training":
            name = requirement["name"]
            expiry = request["training"].get(name)
            if expiry is None or expiry < request["timestamp"]:
                return f"training {name} expired or missing"
            return None

        elif kind == "escort":
            failed = not request["escort"]

        elif kind == "window":
            failed = not cls._inside_window(requirement, request["timestamp"])

        elif kind == "closure":
            timestamp = request["timestamp"]
            failed = requirement["start"] <= timestamp < requirement["end"]

        else:
            raise ValueError(f"unsupported requirement type: {kind}")

        return cls._FAILURE_MESSAGES[kind] if failed else None

    def _failures_and_overrides(
        self, request: Mapping[str, Any]
    ) -> Tuple[Dict[str, str], Sequence[Mapping[str, Any]]]:
        failures: Dict[str, str] = {}
        overrides: List[Mapping[str, Any]] = []

        for requirement in self._requirements:
            if requirement["type"] == "emergency_override":
                overrides.append(requirement)
                continue

            message = self._failure(requirement, request)
            if message is not None:
                failures[requirement["id"]] = message

        return failures, overrides

    def evaluate(
        self, request: Mapping[str, Any], emergency: bool = False
    ) -> Dict[str, Any]:
        failures, overrides = self._failures_and_overrides(request)
        overridden = set()
        override_explanations: Dict[str, str] = {}

        if emergency:
            failed_ids = set(failures)
            for requirement in overrides:
                matching = sorted(set(requirement["overrides"]) & failed_ids)
                if matching:
                    overridden.update(matching)
                    override_explanations[requirement["id"]] = (
                        "emergency override of " + ", ".join(matching)
                    )

        explanation_items = {
            requirement_id: message
            for requirement_id, message in failures.items()
            if requirement_id not in overridden
        }
        explanation_items.update(override_explanations)

        explanation = [
            f"{requirement_id}: {explanation_items[requirement_id]}"
            for requirement_id in sorted(explanation_items)
        ]

        return {
            "allowed": all(
                requirement_id in overridden for requirement_id in failures
            ),
            "explanation": explanation,
        }

    def evaluate_batch(
        self,
        requests: Iterable[Mapping[str, Any]],
        emergency: bool = False,
    ) -> List[Dict[str, Any]]:
        return [
            self.evaluate(request, emergency=emergency)
            for request in requests
        ]

    def evaluate_with(
        self,
        request: Mapping[str, Any],
        replacements: Mapping[str, Any],
        emergency: bool = False,
    ) -> Dict[str, Any]:
        hypothetical = dict(request)
        hypothetical.update(replacements)
        return self.evaluate(hypothetical, emergency=emergency)

    def affected_by_emergency(
        self, request: Mapping[str, Any]
    ) -> List[str]:
        failures, overrides = self._failures_and_overrides(request)
        failed_ids = set(failures)
        affected = set()

        for requirement in overrides:
            affected.update(set(requirement["overrides"]) & failed_ids)

        return sorted(affected)
