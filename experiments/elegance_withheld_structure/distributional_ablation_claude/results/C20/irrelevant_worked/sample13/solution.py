"""Console-oriented patient check-in component with replaceable I/O adapters."""

_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}


class CheckInConsole:
    """Accepts structured check-in actions and emits deterministic screens."""

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    # -- screen construction -------------------------------------------------

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == "Choose appointment":
            choices = [
                {"id": a["id"], "label": a["label"]} for a in self._appointments
            ]
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error(self, reason):
        return self._screen("Error", ["Error: " + reason])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    # -- validation helpers --------------------------------------------------

    def _known_appointment(self, appointment_id):
        return any(a["id"] == appointment_id for a in self._appointments)

    def _open_session_error(self):
        if not self.session["active"]:
            return self._error("no active session")
        if self.session["completed"]:
            return self._error("session already completed")
        return None

    # -- action handling -----------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        kind = action.get("type")
        if kind == "begin":
            return self._begin()
        if kind == "confirm_contact":
            return self._confirm_contact(action)
        if kind == "choose_appointment":
            return self._choose_appointment(action)
        if kind == "correct":
            return self._correct(action)
        if kind == "complete":
            return self._complete()
        if kind == "abandon":
            return self._abandon()
        return self._error("unknown action type")

    def _begin(self):
        if self.session["active"]:
            return self._error("session already active")
        self.session = dict(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _confirm_contact(self, action):
        error = self._open_session_error()
        if error is not None:
            return error
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(
            "Choose appointment" if confirmed else "Confirm contact"
        )

    def _choose_appointment(self, action):
        error = self._open_session_error()
        if error is not None:
            return error
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or not self._known_appointment(
            appointment_id
        ):
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _correct(self, action):
        error = self._open_session_error()
        if error is not None:
            return error
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' requires a boolean value")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or not self._known_appointment(value)
            ):
                return self._error(
                    "'appointment_id' requires a known appointment id or None"
                )
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _complete(self):
        error = self._open_session_error()
        if error is not None:
            return error
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completion")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completion")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _abandon(self):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = dict(_INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # -- console loop --------------------------------------------------------

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink"
            )
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
