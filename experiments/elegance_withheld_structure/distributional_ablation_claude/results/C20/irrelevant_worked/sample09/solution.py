"""Console-oriented patient check-in component with deterministic screens."""


class CheckInConsole:
    """Processes structured check-in actions and emits screen descriptions.

    Input acquisition (``action_source.read_action()``) and output display
    (``screen_sink.display(screen)``) are injected so tests can supply fakes.
    """

    _INITIAL_SESSION = {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": item["id"], "label": item["label"]} for item in appointments
        ]
        self._appointment_ids = {item["id"] for item in self._appointments}
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = dict(self._INITIAL_SESSION)

    # -- public API ---------------------------------------------------------

    def handle(self, action):
        """Apply one structured action and return the resulting screen."""
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        handler = {
            "begin": self._handle_begin,
            "confirm_contact": self._handle_confirm_contact,
            "choose_appointment": self._handle_choose_appointment,
            "correct": self._handle_correct,
            "complete": self._handle_complete,
            "abandon": self._handle_abandon,
        }.get(action.get("type"))
        if handler is None:
            return self._error("unknown action type")
        return handler(action)

    def run_once(self):
        """Read one action, handle it, display the screen, and return it."""
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink"
            )
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen

    # -- action handlers ----------------------------------------------------

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = dict(self._INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _handle_confirm_contact(self, action):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(
            "Choose appointment" if confirmed else "Confirm contact"
        )

    def _handle_choose_appointment(self, action):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        appointment_id = action.get("appointment_id")
        if (
            not isinstance(appointment_id, str)
            or appointment_id not in self._appointment_ids
        ):
            return self._error("'appointment_id' must be a known appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _handle_correct(self, action):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error(
                    "'contact_confirmed' correction requires a boolean value"
                )
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str)
                or value not in self._appointment_ids
            ):
                return self._error(
                    "'appointment_id' correction requires a known id or None"
                )
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session = dict(self._INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # -- screen construction ------------------------------------------------

    def _screen(self, prompt, messages=None):
        choices = (
            [dict(item) for item in self._appointments]
            if prompt == "Choose appointment"
            else []
        )
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error(self, reason):
        return self._screen(self._current_prompt(), ["Error: " + reason])

    def _current_prompt(self):
        if self.session["completed"]:
            return "Check-in complete"
        if not self.session["active"]:
            return "Begin check-in"
        return self._next_prompt()

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    # -- validation helpers -------------------------------------------------

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return self._error("no active session")
        if self.session["completed"]:
            return self._error("session already completed")
        return None
