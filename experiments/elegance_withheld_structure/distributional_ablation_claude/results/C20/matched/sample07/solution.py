"""Console-oriented patient check-in component.

Separates authoritative session data, screen construction, and action
interpretation so input and output can be replaced by test fakes.
"""


class CheckInConsole:
    """Interprets structured check-in actions against patient session data
    and emits deterministic screen descriptions."""

    _PROMPT_CONTACT = "Confirm contact"
    _PROMPT_APPOINTMENT = "Choose appointment"
    _PROMPT_REVIEW = "Review and complete"
    _PROMPT_COMPLETE = "Check-in complete"
    _PROMPT_ABANDONED = "Session abandoned"
    _PROMPT_ERROR = "Error"

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._appointment_ids = {a["id"] for a in self._appointments}
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

    # -- screen construction -------------------------------------------------

    def _screen(self, prompt, messages=()):
        if prompt == self._PROMPT_APPOINTMENT:
            choices = [dict(a) for a in self._appointments]
        else:
            choices = []
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _error(self, detail):
        return self._screen(self._PROMPT_ERROR, ["Error: " + detail])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return self._PROMPT_CONTACT
        if self.session["appointment_id"] is None:
            return self._PROMPT_APPOINTMENT
        return self._PROMPT_REVIEW

    # -- action interpretation ----------------------------------------------

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

    def _require_open_session(self):
        if not self.session["active"]:
            return self._error("no active session")
        if self.session["completed"]:
            return self._error("session already completed")
        return None

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("session already active")
        self.session = self._initial_session()
        self.session["active"] = True
        return self._screen(self._PROMPT_CONTACT)

    def _handle_confirm_contact(self, action):
        blocked = self._require_open_session()
        if blocked is not None:
            return blocked
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        prompt = self._PROMPT_APPOINTMENT if confirmed else self._PROMPT_CONTACT
        return self._screen(prompt)

    def _handle_choose_appointment(self, action):
        blocked = self._require_open_session()
        if blocked is not None:
            return blocked
        appointment_id = action.get("appointment_id")
        if appointment_id not in self._appointment_ids:
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen(self._PROMPT_REVIEW)

    def _handle_correct(self, action):
        blocked = self._require_open_session()
        if blocked is not None:
            return blocked
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' value must be a boolean")
        elif field == "appointment_id":
            if value is not None and value not in self._appointment_ids:
                return self._error("'appointment_id' value must be a known id or None")
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        blocked = self._require_open_session()
        if blocked is not None:
            return blocked
        if self.session["contact_confirmed"] is not True:
            return self._error("contact not confirmed")
        if self.session["appointment_id"] is None:
            return self._error("no appointment selected")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(self._PROMPT_COMPLETE, ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = self._initial_session()
        return self._screen(self._PROMPT_ABANDONED, ["Check-in abandoned"])

    # -- replaceable input/output ports -------------------------------------

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires both action_source and screen_sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
