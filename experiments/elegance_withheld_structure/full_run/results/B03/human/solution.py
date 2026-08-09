from copy import deepcopy


class AccessService:
    _FAILURE_MESSAGES = {
        "badge": "badge not allowed",
        "escort": "escort required",
        "window": "outside access window",
        "closure": "temporarily closed",
    }

    def __init__(self, requirements):
        copied = deepcopy(list(requirements))
        seen_ids = set()
        normalized = []

        for requirement in copied:
            if not isinstance(requirement, dict):
                raise TypeError("each requirement must be a dictionary")

            requirement_id = requirement.get("id")
            if not isinstance(requirement_id, str) or not requirement_id:
                raise ValueError("each requirement must have a non-empty string id")
            if requirement_id in seen_ids:
                raise ValueError("requirement ids must be unique")
            seen_ids.add(requirement_id)

            requirement_type = requirement.get("type")
            if requirement_type not in {
                "badge",
                "training",
                "escort",
                "window",
                "closure",
                "emergency_override",
            }:
                raise ValueError("unsupported requirement type: %r" % requirement_type)

            if requirement_type == "window":
                weekdays = frozenset(requirement["weekdays"])
                if any(not isinstance(day, int) or day < 0 or day > 6 for day in weekdays):
                    raise ValueError("window weekdays must be integers from 0 through 6")
                requirement["weekdays"] = weekdays
                requirement["start_minutes"] = self._parse_time(requirement["start"])
                requirement["end_minutes"] = self._parse_time(requirement["end"])
            elif requirement_type == "badge":
                requirement["allowed"] = frozenset(requirement["allowed"])
            elif requirement_type == "emergency_override":
                requirement["overrides"] = frozenset(requirement["overrides"])

            normalized.append(requirement)

        self._requirements = tuple(sorted(normalized, key=lambda item: item["id"]))
        self._overrides = tuple(
            requirement
            for requirement in self._requirements
            if requirement["type"] == "emergency_override"
        )

    @staticmethod
    def _parse_time(value):
        if not isinstance(value, str):
            raise ValueError("window times must use HH:MM")
        parts = value.split(":")
        if len(parts) != 2 or any(len(part) != 2 or not part.isdigit() for part in parts):
            raise ValueError("window times must use HH:MM")
        hour, minute = map(int, parts)
        if hour > 23 or minute > 59:
            raise ValueError("window times must use HH:MM")
        return hour * 60 + minute

    @staticmethod
    def _inside_window(timestamp, requirement):
        current = timestamp.hour * 60 + timestamp.minute
        weekday = timestamp.weekday()
        weekdays = requirement["weekdays"]
        start = requirement["start_minutes"]
        end = requirement["end_minutes"]

        if start == end:
            return weekday in weekdays
        if start < end:
            return weekday in weekdays and start <= current < end

        return (
            weekday in weekdays and current >= start
        ) or (
            (weekday - 1) % 7 in weekdays and current < end
        )

    def _failures(self, request):
        failures = {}
        timestamp = request["timestamp"]

        for requirement in self._requirements:
            requirement_id = requirement["id"]
            requirement_type = requirement["type"]

            if requirement_type == "emergency_override":
                continue

            failed = False
            message = self._FAILURE_MESSAGES.get(requirement_type)

            if requirement_type == "badge":
                failed = request["badge"] not in requirement["allowed"]
            elif requirement_type == "training":
                name = requirement["name"]
                expiry = request["training"].get(name)
                failed = expiry is None or expiry < timestamp
                message = "training %s expired or missing" % name
            elif requirement_type == "escort":
                failed = not request["escort"]
            elif requirement_type == "window":
                failed = not self._inside_window(timestamp, requirement)
            elif requirement_type == "closure":
                failed = requirement["start"] <= timestamp < requirement["end"]

            if failed:
                failures[requirement_id] = message

        return failures

    def evaluate(self, request, emergency=False):
        failures = self._failures(request)
        explanation_items = []
        overridden = set()

        if emergency:
            failed_ids = set(failures)
            for override in self._overrides:
                affected = sorted(failed_ids.intersection(override["overrides"]))
                if affected:
                    overridden.update(affected)
                    explanation_items.append(
                        (
                            override["id"],
                            "emergency override of %s" % ", ".join(affected),
                        )
                    )

        blocking = set(failures).difference(overridden)
        explanation_items.extend(
            (requirement_id, failures[requirement_id])
            for requirement_id in blocking
        )
        explanation_items.sort(key=lambda item: item[0])

        return {
            "allowed": not blocking,
            "explanation": [
                "%s: %s" % (requirement_id, message)
                for requirement_id, message in explanation_items
            ],
        }

    def evaluate_batch(self, requests, emergency=False):
        return [self.evaluate(request, emergency=emergency) for request in requests]

    def evaluate_with(self, request, replacements, emergency=False):
        hypothetical = request.copy()
        hypothetical.update(replacements)
        return self.evaluate(hypothetical, emergency=emergency)

    def affected_by_emergency(self, request):
        failed_ids = set(self._failures(request))
        affected = set()
        for override in self._overrides:
            affected.update(failed_ids.intersection(override["overrides"]))
        return sorted(affected)
