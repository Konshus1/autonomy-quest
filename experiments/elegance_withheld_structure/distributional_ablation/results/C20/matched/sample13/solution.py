"""Deterministic console-oriented patient check-in component."""


class CheckInConsole:
    """Interpret structured check-in actions and return screen descriptions."""

    _INITIAL_SESSION = {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }

    _ACTION_TYPES = {
        "begin",
        "confirm_contact",
        "choose_appointment",
        "correct",
        "complete",
        "abandon",
    }

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self.appointments = [dict(appointment) for appointment in appointments]
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(self._INITIAL_SESSION)

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == "Choose appointment":
            choices = [
                {"id": appointment["id"], "label": appointment["label"]}
                for appointment in self.appointments
            ]

        return {
            "prompt": prompt,
            "messages": [] if messages is None else list(messages),
            "choices": choices,
        }

    def _current_prompt(self, session=None):
        state = self.session if session is None else session

        if not state["active"]:
            if state["completed"]:
                return "Check-in complete"
            return "Begin check-in"
        if state["contact_confirmed"] is not True:
            return "Confirm contact"
        if state["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    def _error(self, message):
        return self._screen(self._current_prompt(), ["Error: " + message])

    @staticmethod
    def _is_boolean(value):
        return type(value) is bool

    def _known_appointment(self, appointment_id):
        return any(
            appointment["id"] == appointment_id
            for appointment in self.appointments
        )

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")

        action_type = action.get("type")
        if not isinstance(action_type, str) or action_type not in self._ACTION_TYPES:
            return self._error("unknown or missing action type")

        candidate = dict(self.session)

        if action_type == "begin":
            if self.session["active"]:
                return self._error("a session is already active")

            candidate = dict(self._INITIAL_SESSION)
            candidate["active"] = True
            self.session = candidate
            return self._screen("Confirm contact")

        if action_type == "abandon":
            if not self.session["active"]:
                return self._error("no active session to abandon")

            self.session = dict(self._INITIAL_SESSION)
            return self._screen(
                "Session abandoned",
                ["Check-in abandoned"],
            )

        if not self.session["active"] or self.session["completed"]:
            return self._error("an active, incomplete session is required")

        if action_type == "confirm_contact":
            confirmed = action.get("confirmed")
            if not self._is_boolean(confirmed):
                return self._error("confirmed must be a boolean")

            candidate["contact_confirmed"] = confirmed
            self.session = candidate
            prompt = "Choose appointment" if confirmed else "Confirm contact"
            return self._screen(prompt)

        if action_type == "choose_appointment":
            appointment_id = action.get("appointment_id")
            if not isinstance(appointment_id, str):
                return self._error("appointment_id must be a string")
            if not self._known_appointment(appointment_id):
                return self._error("appointment_id is not known")

            candidate["appointment_id"] = appointment_id
            self.session = candidate
            return self._screen("Review and complete")

        if action_type == "correct":
            field = action.get("field")
            value = action.get("value")

            if field == "contact_confirmed":
                if not self._is_boolean(value):
                    return self._error(
                        "contact_confirmed correction must be boolean"
                    )
                candidate[field] = value
            elif field == "appointment_id":
                if value is not None and (
                    not isinstance(value, str)
                    or not self._known_appointment(value)
                ):
                    return self._error(
                        "appointment_id correction must be known or None"
                    )
                candidate[field] = value
            else:
                return self._error("field is not correctable")

            self.session = candidate
            return self._screen(self._current_prompt(candidate))

        if self.session["contact_confirmed"] is not True:
            return self._error(
                "contact must be confirmed before completion"
            )
        if self.session["appointment_id"] is None:
            return self._error(
                "an appointment must be selected before completion"
            )

        candidate["completed"] = True
        candidate["active"] = False
        self.session = candidate
        return self._screen("Check-in complete", ["Checked in"])

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action source and screen sink are required")

        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
