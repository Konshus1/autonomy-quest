class AccessService:
    _SUPPORTED_TYPES = {
        "badge",
        "training",
        "escort",
        "window",
        "closure",
        "emergency_override",
    }

    def __init__(self, requirements):
        self._requirements = tuple(dict(requirement) for requirement in requirements)

        ids = [requirement.get("id") for requirement in self._requirements]
        if any(not isinstance(identifier, str) for identifier in ids):
            raise ValueError("requirement IDs must be strings")
        if len(ids) != len(set(ids)):
            raise ValueError("requirement IDs must be unique")
        if any(
            requirement.get("type") not in self._SUPPORTED_TYPES
            for requirement in self._requirements
        ):
            raise ValueError("unsupported requirement type")

    @staticmethod
    def _minutes(value):
        hour, minute = value.split(":")
        return int(hour) * 60 + int(minute)

    def _inside_window(self, requirement, timestamp):
        weekdays = requirement["weekdays"]
        start = self._minutes(requirement["start"])
        end = self._minutes(requirement["end"])
        current = timestamp.hour * 60 + timestamp.minute
        weekday = timestamp.weekday()

        if start == end:
            return weekday in weekdays

        if start < end:
            return weekday in weekdays and start <= current < end

        if current >= start:
            return weekday in weekdays

        if current < end:
            previous_weekday = (weekday - 1) % 7
            return previous_weekday in weekdays

        return False

    def _failure_message(self, requirement, request):
        kind = requirement["type"]

        if kind == "badge":
            if request["badge"] not in requirement["allowed"]:
                return "badge not allowed"

        elif kind == "training":
            name = requirement["name"]
            expiry = request["training"].get(name)
            if expiry is None or expiry < request["timestamp"]:
                return f"training {name} expired or missing"

        elif kind == "escort":
            if not request["escort"]:
                return "escort required"

        elif kind == "window":
            if not self._inside_window(requirement, request["timestamp"]):
                return "outside access window"

        elif kind == "closure":
            if requirement["start"] <= request["timestamp"] < requirement["end"]:
                return "temporarily closed"

        return None

    def _failures(self, request):
        failures = {}
        for requirement in self._requirements:
            if requirement["type"] == "emergency_override":
                continue
            message = self._failure_message(requirement, request)
            if message is not None:
                failures[requirement["id"]] = message
        return failures

    def _override_effects(self, failed_ids):
        effects = {}
        for requirement in self._requirements:
            if requirement["type"] != "emergency_override":
                continue
            overridden = sorted(failed_ids.intersection(requirement["overrides"]))
            if overridden:
                effects[requirement["id"]] = overridden
        return effects

    def evaluate(self, request, emergency=False):
        failures = self._failures(request)
        explanation_entries = dict(failures)

        if emergency:
            effects = self._override_effects(set(failures))
            overridden_ids = {
                identifier
                for identifiers in effects.values()
                for identifier in identifiers
            }
            explanation_entries = {
                identifier: message
                for identifier, message in failures.items()
                if identifier not in overridden_ids
            }
            for override_id, identifiers in effects.items():
                explanation_entries[override_id] = (
                    "emergency override of " + ", ".join(identifiers)
                )

        remaining_failures = set(failures).intersection(explanation_entries)
        explanation = [
            f"{identifier}: {explanation_entries[identifier]}"
            for identifier in sorted(explanation_entries)
        ]
        return {
            "allowed": not remaining_failures,
            "explanation": explanation,
        }

    def evaluate_batch(self, requests, emergency=False):
        return [
            self.evaluate(request, emergency=emergency)
            for request in requests
        ]

    def evaluate_with(self, request, replacements, emergency=False):
        hypothetical = dict(request)
        hypothetical.update(replacements)
        return self.evaluate(hypothetical, emergency=emergency)

    def affected_by_emergency(self, request):
        failures = self._failures(request)
        effects = self._override_effects(set(failures))
        return sorted({
            identifier
            for identifiers in effects.values()
            for identifier in identifiers
        })
