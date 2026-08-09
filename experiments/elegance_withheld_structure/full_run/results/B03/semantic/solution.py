from __future__ import annotations

from datetime import time, timedelta
from typing import Any, Iterable, Mapping


class AccessService:
    def __init__(self, requirements: Iterable[Mapping[str, Any]]) -> None:
        self._requirements = tuple(dict(requirement) for requirement in requirements)

    @staticmethod
    def _inside_window(requirement: Mapping[str, Any], timestamp: Any) -> bool:
        start = time.fromisoformat(requirement["start"])
        end = time.fromisoformat(requirement["end"])
        current_time = timestamp.time()
        allowed_weekdays = set(requirement["weekdays"])

        if start == end:
            return timestamp.weekday() in allowed_weekdays

        if start < end:
            return (
                timestamp.weekday() in allowed_weekdays
                and start <= current_time < end
            )

        if current_time >= start:
            return timestamp.weekday() in allowed_weekdays

        if current_time < end:
            preceding_weekday = (timestamp - timedelta(days=1)).weekday()
            return preceding_weekday in allowed_weekdays

        return False

    def _failures(self, request: Mapping[str, Any]) -> dict[str, str]:
        failures: dict[str, str] = {}
        timestamp = request["timestamp"]

        for requirement in self._requirements:
            requirement_id = requirement["id"]
            requirement_type = requirement["type"]

            if requirement_type == "badge":
                if request["badge"] not in requirement["allowed"]:
                    failures[requirement_id] = "badge not allowed"

            elif requirement_type == "training":
                name = requirement["name"]
                expiry = request["training"].get(name)
                if expiry is None or expiry < timestamp:
                    failures[requirement_id] = (
                        f"training {name} expired or missing"
                    )

            elif requirement_type == "escort":
                if not request["escort"]:
                    failures[requirement_id] = "escort required"

            elif requirement_type == "window":
                if not self._inside_window(requirement, timestamp):
                    failures[requirement_id] = "outside access window"

            elif requirement_type == "closure":
                if requirement["start"] <= timestamp < requirement["end"]:
                    failures[requirement_id] = "temporarily closed"

            elif requirement_type == "emergency_override":
                continue

            else:
                raise ValueError(
                    f"unsupported requirement type: {requirement_type!r}"
                )

        return failures

    def _emergency_effects(
        self, failures: Mapping[str, str]
    ) -> tuple[set[str], list[tuple[str, str]]]:
        overridden: set[str] = set()
        explanations: list[tuple[str, str]] = []

        for requirement in self._requirements:
            if requirement["type"] != "emergency_override":
                continue

            named_failures = sorted(
                set(requirement["overrides"]).intersection(failures)
            )
            if named_failures:
                overridden.update(named_failures)
                explanations.append(
                    (
                        requirement["id"],
                        "emergency override of " + ", ".join(named_failures),
                    )
                )

        return overridden, explanations

    def evaluate(
        self, request: Mapping[str, Any], emergency: bool = False
    ) -> dict[str, Any]:
        failures = self._failures(request)
        entries: list[tuple[str, str]] = []

        if emergency:
            overridden, override_entries = self._emergency_effects(failures)
            entries.extend(
                (requirement_id, message)
                for requirement_id, message in failures.items()
                if requirement_id not in overridden
            )
            entries.extend(override_entries)
        else:
            entries.extend(failures.items())

        entries.sort(key=lambda entry: entry[0])
        return {
            "allowed": not any(
                requirement_id in failures
                for requirement_id, _ in entries
            ),
            "explanation": [
                f"{requirement_id}: {message}"
                for requirement_id, message in entries
            ],
        }

    def evaluate_batch(
        self,
        requests: Iterable[Mapping[str, Any]],
        emergency: bool = False,
    ) -> list[dict[str, Any]]:
        return [self.evaluate(request, emergency) for request in requests]

    def evaluate_with(
        self,
        request: Mapping[str, Any],
        replacements: Mapping[str, Any],
        emergency: bool = False,
    ) -> dict[str, Any]:
        hypothetical = dict(request)
        hypothetical.update(replacements)
        return self.evaluate(hypothetical, emergency)

    def affected_by_emergency(self, request: Mapping[str, Any]) -> list[str]:
        failures = self._failures(request)
        affected, _ = self._emergency_effects(failures)
        return sorted(affected)
