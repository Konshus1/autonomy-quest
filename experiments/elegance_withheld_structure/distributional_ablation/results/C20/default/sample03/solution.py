"""Deterministic console-oriented patient check-in state machine."""


class CheckInConsole:
    _INITIAL_SESSION = {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self.appointments = [dict(appointment) for appointment in appointments]
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(self._INITIAL_SESSION)

    def _choices(self):
        return [
            {"id": appointment["id"], "label": appointment["label"]}
            for appointment in self.appointments
        ]

    def _screen(self, prompt, messages=None):
        return {
            "prompt": prompt,
            "messages": [] if messages is None else list(messages),
            "choices": self._choices() if prompt == "Choose appointment" else [],
        }

    def _current_prompt(self):
        if self.session["completed"]:
            return "Check-in complete"
        if not self.session["active"]:
            return "Begin check-in"
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    def _error(self, explanation):
        return self._screen(self._current_prompt(), ["Error: " + explanation])

    def _active_incomplete_error(self):
        if not self.session["active"]:
            return "no check-in session is active"
        if self.session["completed"]:
            return "the check-in session is already complete"
        return None

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")

        previous_session = dict(self.session)
        try:
            action_type = action.get("type")
            if not isinstance(action_type, str):
                return self._error("action type must be a string")

            handlers = {
                "begin": self._begin,
                "confirm_contact": self._confirm_contact,
                "choose_appointment": self._choose_appointment,
                "correct": self._correct,
                "complete": self._complete,
                "abandon": self._abandon,
            }
            handler = handlers.get(action_type)
            if handler is None:
                return self._error("unknown action type")
            return handler(action)
        except Exception:
            self.session = previous_session
            return self._error("malformed action")

    def _begin(self, action):
        if self.session["active"]:
            return self._error("a check-in session is already active")

        self.session = dict(self._INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _confirm_contact(self, action):
        problem = self._active_incomplete_error()
        if problem:
            return self._error(problem)
        if "confirmed" not in action or type(action["confirmed"]) is not bool:
            return self._error("confirmed must be a boolean")

        self.session["contact_confirmed"] = action["confirmed"]
        prompt = (
            "Choose appointment"
            if action["confirmed"]
            else "Confirm contact"
        )
        return self._screen(prompt)

    def _choose_appointment(self, action):
        problem = self._active_incomplete_error()
        if problem:
            return self._error(problem)
        if self.session["contact_confirmed"] is not True:
            return self._error(
                "contact must be confirmed before choosing an appointment"
            )

        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str):
            return self._error("appointment_id must be a string")
        if appointment_id not in {item["id"] for item in self.appointments}:
            return self._error("appointment_id is not known")

        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _correct(self, action):
        problem = self._active_incomplete_error()
        if problem:
            return self._error(problem)
        if "field" not in action or "value" not in action:
            return self._error("correction requires field and value")

        field = action["field"]
        value = action["value"]

        if field == "contact_confirmed":
            if type(value) is not bool:
                return self._error(
                    "contact_confirmed correction must be boolean"
                )
            self.session["contact_confirmed"] = value
        elif field == "appointment_id":
            known_ids = {item["id"] for item in self.appointments}
            if value is not None and (
                not isinstance(value, str) or value not in known_ids
            ):
                return self._error(
                    "appointment_id correction must be a known id or None"
                )
            self.session["appointment_id"] = value
        else:
            return self._error("field is not correctable")

        return self._screen(self._current_prompt())

    def _complete(self, action):
        problem = self._active_incomplete_error()
        if problem:
            return self._error(problem)
        if self.session["contact_confirmed"] is not True:
            return self._error("contact has not been confirmed")
        if self.session["appointment_id"] is None:
            return self._error("an appointment has not been selected")

        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _abandon(self, action):
        if not self.session["active"]:
            return self._error("no check-in session is active")

        self.session = dict(self._INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink"
            )

        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
