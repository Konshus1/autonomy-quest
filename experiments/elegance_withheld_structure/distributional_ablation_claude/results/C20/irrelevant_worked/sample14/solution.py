"""Console-oriented patient check-in component with injectable I/O adapters."""


def _initial_session():
    return {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._ids = {a["id"] for a in self._appointments}
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = _initial_session()

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

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    def _current_prompt(self):
        if self.session["completed"]:
            return "Check-in complete"
        if not self.session["active"]:
            return "Welcome"
        return self._next_prompt()

    def _error(self, reason):
        return self._screen(self._current_prompt(), ["Error: " + reason])

    # -- action handling -----------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        kind = action.get("type")
        if kind == "begin":
            return self._handle_begin()
        if kind == "confirm_contact":
            return self._handle_confirm_contact(action)
        if kind == "choose_appointment":
            return self._handle_choose_appointment(action)
        if kind == "correct":
            return self._handle_correct(action)
        if kind == "complete":
            return self._handle_complete()
        if kind == "abandon":
            return self._handle_abandon()
        return self._error("unknown action type")

    def _require_open_session(self):
        if not self.session["active"] or self.session["completed"]:
            return self._error("no active check-in session")
        return None

    def _handle_begin(self):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = _initial_session()
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _handle_confirm_contact(self, action):
        blocked = self._require_open_session()
        if blocked is not None:
            return blocked
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("confirmed must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen("Choose appointment" if confirmed else "Confirm contact")

    def _handle_choose_appointment(self, action):
        blocked = self._require_open_session()
        if blocked is not None:
            return blocked
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._ids:
            return self._error("unknown appointment_id")
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _handle_correct(self, action):
        blocked = self._require_open_session()
        if blocked is not None:
            return blocked
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("contact_confirmed correction requires a boolean")
            self.session["contact_confirmed"] = value
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._ids
            ):
                return self._error("appointment_id correction requires a known id or None")
            self.session["appointment_id"] = value
        else:
            return self._error("unknown correction field")
        return self._screen(self._next_prompt())

    def _handle_complete(self):
        blocked = self._require_open_session()
        if blocked is not None:
            return blocked
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _handle_abandon(self):
        if not self.session["active"]:
            return self._error("no active check-in session")
        self.session = _initial_session()
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # -- console loop step ---------------------------------------------------

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires both an action source and a screen sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
