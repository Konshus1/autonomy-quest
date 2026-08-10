"""Console-oriented patient check-in component with replaceable I/O ports."""

_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}


class CheckInConsole:
    """Interprets structured check-in actions against patient session data
    and emits deterministic screen descriptions."""

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    # -- screen construction -------------------------------------------------

    def _screen(self, prompt, messages=()):
        if prompt == "Choose appointment":
            choices = [dict(a) for a in self._appointments]
        else:
            choices = []
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _error(self, reason):
        return self._screen("Error", ["Error: " + reason])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    def _known_id(self, appointment_id):
        return any(a["id"] == appointment_id for a in self._appointments)

    # -- action handling -----------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        kind = action.get("type")
        session = self.session

        if kind == "begin":
            if session["active"]:
                return self._error("a session is already active")
            self.session = dict(_INITIAL_SESSION)
            self.session["active"] = True
            return self._screen("Confirm contact")

        if kind == "confirm_contact":
            if not session["active"] or session["completed"]:
                return self._error("no active session to confirm contact for")
            confirmed = action.get("confirmed")
            if not isinstance(confirmed, bool):
                return self._error("'confirmed' must be a boolean")
            session["contact_confirmed"] = confirmed
            return self._screen(
                "Choose appointment" if confirmed else "Confirm contact"
            )

        if kind == "choose_appointment":
            if not session["active"] or session["completed"]:
                return self._error("no active session to choose an appointment for")
            appointment_id = action.get("appointment_id")
            if not isinstance(appointment_id, str) or not self._known_id(
                appointment_id
            ):
                return self._error("unknown appointment id")
            session["appointment_id"] = appointment_id
            return self._screen("Review and complete")

        if kind == "correct":
            if not session["active"] or session["completed"]:
                return self._error("no active session to correct")
            field = action.get("field")
            value = action.get("value")
            if field == "contact_confirmed":
                if not isinstance(value, bool):
                    return self._error("'contact_confirmed' value must be a boolean")
            elif field == "appointment_id":
                if value is not None and not (
                    isinstance(value, str) and self._known_id(value)
                ):
                    return self._error(
                        "'appointment_id' value must be a known id or None"
                    )
            else:
                return self._error("unknown correction field")
            session[field] = value
            return self._screen(self._next_prompt())

        if kind == "complete":
            if not session["active"] or session["completed"]:
                return self._error("no active session to complete")
            if session["contact_confirmed"] is not True:
                return self._error("contact must be confirmed before completing")
            if session["appointment_id"] is None:
                return self._error("an appointment must be chosen before completing")
            session["completed"] = True
            session["active"] = False
            return self._screen("Check-in complete", ["Checked in"])

        if kind == "abandon":
            if not session["active"]:
                return self._error("no active session to abandon")
            self.session = dict(_INITIAL_SESSION)
            return self._screen("Session abandoned", ["Check-in abandoned"])

        return self._error("unknown action type")

    # -- one interaction cycle over the injected ports -----------------------

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink"
            )
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
