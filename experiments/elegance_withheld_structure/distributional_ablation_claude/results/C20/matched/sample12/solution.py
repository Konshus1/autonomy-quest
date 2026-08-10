"""Console-oriented patient check-in component with replaceable I/O ports."""

_ACTION_TYPES = {
    "begin",
    "confirm_contact",
    "choose_appointment",
    "correct",
    "complete",
    "abandon",
}
_MISSING = object()


class CheckInConsole:
    """Maintains check-in session data and emits deterministic screen dicts."""

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [{"id": a["id"], "label": a["label"]} for a in appointments]
        self._known_ids = {a["id"] for a in self._appointments}
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = self._initial_session()

    @staticmethod
    def _initial_session():
        return {
            "active": False,
            "contact_confirmed": None,
            "appointment_id": None,
            "completed": False,
        }

    # ------------------------------------------------------------------ actions

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        action_type = action.get("type")
        if action_type not in _ACTION_TYPES:
            return self._error("unknown action type")
        return getattr(self, "_do_" + action_type)(action)

    def _do_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = self._initial_session()
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _do_confirm_contact(self, action):
        error = self._require_active_incomplete()
        if error is not None:
            return error
        confirmed = action.get("confirmed", _MISSING)
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen("Choose appointment" if confirmed else "Confirm contact")

    def _do_choose_appointment(self, action):
        error = self._require_active_incomplete()
        if error is not None:
            return error
        appointment_id = action.get("appointment_id", _MISSING)
        if not isinstance(appointment_id, str) or appointment_id not in self._known_ids:
            return self._error("'appointment_id' must be a known appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _do_correct(self, action):
        error = self._require_active_incomplete()
        if error is not None:
            return error
        field = action.get("field")
        value = action.get("value", _MISSING)
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("correction of 'contact_confirmed' needs a boolean value")
        elif field == "appointment_id":
            valid = value is None or (isinstance(value, str) and value in self._known_ids)
            if not valid:
                return self._error("correction of 'appointment_id' needs a known id or None")
        else:
            return self._error("correction 'field' must be 'contact_confirmed' or 'appointment_id'")
        self.session[field] = value
        return self._screen(self._next_unmet_prompt())

    def _do_complete(self, action):
        error = self._require_active_incomplete()
        if error is not None:
            return error
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _do_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session = self._initial_session()
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # ------------------------------------------------------------------ guards

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return self._error("no active session")
        if self.session["completed"]:
            return self._error("session is already completed")
        return None

    # ------------------------------------------------------------------ screens

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == "Choose appointment":
            choices = [{"id": a["id"], "label": a["label"]} for a in self._appointments]
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error(self, detail):
        return self._screen(self._current_prompt(), ["Error: " + detail])

    def _current_prompt(self):
        if self.session["completed"]:
            return "Check-in complete"
        if not self.session["active"]:
            return "Begin check-in"
        return self._next_unmet_prompt()

    def _next_unmet_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    # ------------------------------------------------------------------ ports

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires both an action_source and a screen_sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
