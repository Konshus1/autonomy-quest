"""Console-oriented patient check-in component with replaceable I/O."""

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
            {"id": item["id"], "label": item["label"]} for item in appointments
        ]
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    # -- screen construction -------------------------------------------------

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == "Choose appointment":
            choices = [
                {"id": item["id"], "label": item["label"]}
                for item in self._appointments
            ]
        return {"prompt": prompt, "messages": list(messages or []), "choices": choices}

    def _next_prompt(self):
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
        return self._screen(self._next_prompt(), ["Error: " + reason])

    # -- action handling -----------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        handlers = {
            "begin": self._handle_begin,
            "confirm_contact": self._handle_confirm_contact,
            "choose_appointment": self._handle_choose_appointment,
            "correct": self._handle_correct,
            "complete": self._handle_complete,
            "abandon": self._handle_abandon,
        }
        handler = handlers.get(action.get("type"))
        if handler is None:
            return self._error("unknown action type: %r" % (action.get("type"),))
        return handler(action)

    def _require_active(self):
        if not self.session["active"] or self.session["completed"]:
            return self._error("no active check-in session")
        return None

    def _known_appointment(self, appointment_id):
        return any(item["id"] == appointment_id for item in self._appointments)

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = dict(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _handle_confirm_contact(self, action):
        blocked = self._require_active()
        if blocked is not None:
            return blocked
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen("Choose appointment" if confirmed else "Confirm contact")

    def _handle_choose_appointment(self, action):
        blocked = self._require_active()
        if blocked is not None:
            return blocked
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or not self._known_appointment(
            appointment_id
        ):
            return self._error("unknown appointment id: %r" % (appointment_id,))
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _handle_correct(self, action):
        blocked = self._require_active()
        if blocked is not None:
            return blocked
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' value must be a boolean")
            self.session["contact_confirmed"] = value
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or not self._known_appointment(value)
            ):
                return self._error(
                    "'appointment_id' value must be a known id or None"
                )
            self.session["appointment_id"] = value
        else:
            return self._error("unknown correction field: %r" % (field,))
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        blocked = self._require_active()
        if blocked is not None:
            return blocked
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
        self.session = dict(_INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # -- console loop --------------------------------------------------------

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError("run_once requires an action source and a screen sink")
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
