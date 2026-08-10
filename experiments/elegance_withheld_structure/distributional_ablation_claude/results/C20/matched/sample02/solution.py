"""Console-oriented patient check-in component with replaceable I/O ports."""


class CheckInConsole:
    """Interprets structured check-in actions against a patient session and
    emits deterministic screen descriptions."""

    _INITIAL_SESSION = {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._appointment_ids = {a["id"] for a in self._appointments}
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(self._INITIAL_SESSION)

    # -- screen construction -------------------------------------------------

    def _screen(self, prompt, messages):
        choices = (
            [dict(a) for a in self._appointments]
            if prompt == "Choose appointment"
            else []
        )
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    def _state_prompt(self):
        if self.session["completed"]:
            return "Check-in complete"
        if not self.session["active"]:
            return "Begin check-in"
        return self._next_prompt()

    def _error(self, reason):
        return self._screen(self._state_prompt(), ["Error: " + reason])

    # -- action handling -----------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        handler = {
            "begin": self._begin,
            "confirm_contact": self._confirm_contact,
            "choose_appointment": self._choose_appointment,
            "correct": self._correct,
            "complete": self._complete,
            "abandon": self._abandon,
        }.get(action.get("type"))
        if handler is None:
            return self._error("unknown action type %r" % (action.get("type"),))
        return handler(action)

    def _begin(self, action):
        if self.session["active"]:
            return self._error("session already active")
        self.session = dict(self._INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact", [])

    def _require_open(self):
        if not self.session["active"]:
            return "no active session"
        if self.session["completed"]:
            return "session already completed"
        return None

    def _confirm_contact(self, action):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("confirmed must be a boolean")
        self.session["contact_confirmed"] = confirmed
        prompt = "Choose appointment" if confirmed else "Confirm contact"
        return self._screen(prompt, [])

    def _choose_appointment(self, action):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        appointment_id = action.get("appointment_id")
        if appointment_id not in self._appointment_ids:
            return self._error("unknown appointment id %r" % (appointment_id,))
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete", [])

    def _correct(self, action):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("contact_confirmed value must be a boolean")
        elif field == "appointment_id":
            if value is not None and value not in self._appointment_ids:
                return self._error("unknown appointment id %r" % (value,))
        else:
            return self._error("cannot correct field %r" % (field,))
        self.session[field] = value
        return self._screen(self._next_prompt(), [])

    def _complete(self, action):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completion")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completion")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = dict(self._INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # -- console loop --------------------------------------------------------

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action_source and screen_sink are required")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
