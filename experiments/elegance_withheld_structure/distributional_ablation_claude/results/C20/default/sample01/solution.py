"""Console-oriented patient check-in component with injectable I/O ports."""


class CheckInConsole:
    """Structured check-in state machine emitting deterministic screens."""

    _PROMPT_CONTACT = "Confirm contact"
    _PROMPT_APPOINTMENT = "Choose appointment"
    _PROMPT_REVIEW = "Review and complete"

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self.appointments = [dict(a) for a in appointments]
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

    def _known_ids(self):
        return {a["id"] for a in self.appointments}

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == self._PROMPT_APPOINTMENT:
            choices = [{"id": a["id"], "label": a["label"]} for a in self.appointments]
        return {"prompt": prompt, "messages": list(messages or []), "choices": choices}

    def _error(self, reason):
        return self._screen("Error", ["Error: " + reason])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return self._PROMPT_CONTACT
        if self.session["appointment_id"] is None:
            return self._PROMPT_APPOINTMENT
        return self._PROMPT_REVIEW

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return "no active session"
        if self.session["completed"]:
            return "session already completed"
        return None

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
            return self._error("unknown action type")
        return handler(action)

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("session already active")
        self.session = self._initial_session()
        self.session["active"] = True
        return self._screen(self._PROMPT_CONTACT)

    def _handle_confirm_contact(self, action):
        problem = self._require_active_incomplete()
        if problem:
            return self._error(problem)
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        prompt = self._PROMPT_APPOINTMENT if confirmed else self._PROMPT_CONTACT
        return self._screen(prompt)

    def _handle_choose_appointment(self, action):
        problem = self._require_active_incomplete()
        if problem:
            return self._error(problem)
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._known_ids():
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen(self._PROMPT_REVIEW)

    def _handle_correct(self, action):
        problem = self._require_active_incomplete()
        if problem:
            return self._error(problem)
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("correction of 'contact_confirmed' requires a boolean value")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._known_ids()
            ):
                return self._error("correction of 'appointment_id' requires a known id or None")
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        problem = self._require_active_incomplete()
        if problem:
            return self._error(problem)
        if self.session["contact_confirmed"] is not True:
            return self._error("contact not confirmed")
        if self.session["appointment_id"] is None:
            return self._error("no appointment selected")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = self._initial_session()
        return self._screen("Session abandoned", ["Check-in abandoned"])

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action_source and screen_sink are required")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
