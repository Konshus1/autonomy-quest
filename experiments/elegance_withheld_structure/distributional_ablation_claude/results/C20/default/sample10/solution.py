"""Console-oriented patient check-in component with injectable I/O.

The console consumes structured action dictionaries and emits deterministic
screen dictionaries (prompt, messages, choices).  Input and output are
supplied as objects with ``read_action()`` and ``display(screen)`` so tests
can substitute standard-library fakes.
"""

_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [dict(appointment) for appointment in appointments]
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    # -- screen construction -------------------------------------------------

    def _screen(self, prompt, messages=()):
        if prompt == "Choose appointment":
            choices = [{"id": a["id"], "label": a["label"]} for a in self._appointments]
        else:
            choices = []
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

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

    def _error(self, reason):
        return self._screen(self._current_prompt(), ["Error: " + reason])

    def _known_appointment(self, appointment_id):
        return any(a["id"] == appointment_id for a in self._appointments)

    # -- action handling -----------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")

        kind = action.get("type")

        if kind == "begin":
            if self.session["active"]:
                return self._error("a session is already active")
            self.session = dict(_INITIAL_SESSION)
            self.session["active"] = True
            return self._screen("Confirm contact")

        if kind == "abandon":
            if not self.session["active"]:
                return self._error("no active session to abandon")
            self.session = dict(_INITIAL_SESSION)
            return self._screen("Session abandoned", ["Check-in abandoned"])

        if not self.session["active"] or self.session["completed"]:
            return self._error("an active, incomplete session is required")

        if kind == "confirm_contact":
            confirmed = action.get("confirmed")
            if not isinstance(confirmed, bool):
                return self._error("'confirmed' must be a boolean")
            self.session["contact_confirmed"] = confirmed
            return self._screen("Choose appointment" if confirmed else "Confirm contact")

        if kind == "choose_appointment":
            appointment_id = action.get("appointment_id")
            if not isinstance(appointment_id, str) or not self._known_appointment(appointment_id):
                return self._error("'appointment_id' must be a known appointment id")
            self.session["appointment_id"] = appointment_id
            return self._screen("Review and complete")

        if kind == "correct":
            field = action.get("field")
            value = action.get("value")
            if field == "contact_confirmed":
                if not isinstance(value, bool):
                    return self._error("corrected 'contact_confirmed' must be a boolean")
            elif field == "appointment_id":
                if value is not None and (
                    not isinstance(value, str) or not self._known_appointment(value)
                ):
                    return self._error("corrected 'appointment_id' must be a known id or None")
            else:
                return self._error("'field' must be 'contact_confirmed' or 'appointment_id'")
            self.session[field] = value
            return self._screen(self._current_prompt())

        if kind == "complete":
            if self.session["contact_confirmed"] is not True:
                return self._error("contact must be confirmed before completing")
            if self.session["appointment_id"] is None:
                return self._error("an appointment must be chosen before completing")
            self.session["completed"] = True
            self.session["active"] = False
            return self._screen("Check-in complete", ["Checked in"])

        return self._error("unknown action type")

    # -- I/O loop ------------------------------------------------------------

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError("run_once requires both an action_source and a screen_sink")
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
