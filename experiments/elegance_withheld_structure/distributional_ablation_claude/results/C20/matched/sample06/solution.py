"""Console check-in component with replaceable input/output ports.

The session dictionary is the single authoritative record of accepted
check-in data; screens are derived from it on demand and never fed back.
"""

PROMPT_CONTACT = "Confirm contact"
PROMPT_APPOINTMENT = "Choose appointment"
PROMPT_REVIEW = "Review and complete"
PROMPT_COMPLETE = "Check-in complete"
PROMPT_ABANDONED = "Session abandoned"
PROMPT_ERROR = "Error"


def _initial_session():
    return {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [dict(a) for a in appointments]
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = _initial_session()

    # -- screen construction -------------------------------------------

    def _screen(self, prompt, messages=()):
        choices = []
        if prompt == PROMPT_APPOINTMENT:
            choices = [{"id": a["id"], "label": a["label"]}
                       for a in self._appointments]
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _error(self, text):
        return {"prompt": PROMPT_ERROR,
                "messages": ["Error: " + text],
                "choices": []}

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return PROMPT_CONTACT
        if self.session["appointment_id"] is None:
            return PROMPT_APPOINTMENT
        return PROMPT_REVIEW

    def _known_ids(self):
        return [a["id"] for a in self._appointments]

    # -- action handling ------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        action_type = action.get("type")
        if not isinstance(action_type, str):
            return self._error("action type must be a string")
        handlers = {
            "begin": self._do_begin,
            "confirm_contact": self._do_confirm_contact,
            "choose_appointment": self._do_choose_appointment,
            "correct": self._do_correct,
            "complete": self._do_complete,
            "abandon": self._do_abandon,
        }
        handler = handlers.get(action_type)
        if handler is None:
            return self._error("unknown action type: " + action_type)
        return handler(action)

    def _do_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = _initial_session()
        self.session["active"] = True
        return self._screen(PROMPT_CONTACT)

    def _do_confirm_contact(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("no active session to confirm contact for")
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        prompt = PROMPT_APPOINTMENT if confirmed else PROMPT_CONTACT
        return self._screen(prompt)

    def _do_choose_appointment(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("no active session to choose an appointment for")
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._known_ids():
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen(PROMPT_REVIEW)

    def _do_correct(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("no active session to correct")
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' correction requires a boolean value")
        elif field == "appointment_id":
            if value is not None and value not in self._known_ids():
                return self._error("'appointment_id' correction requires a known id or None")
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _do_complete(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("no active session to complete")
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(PROMPT_COMPLETE, ["Checked in"])

    def _do_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session = _initial_session()
        return self._screen(PROMPT_ABANDONED, ["Check-in abandoned"])

    # -- one console interaction -----------------------------------------

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError("run_once requires both an action source and a screen sink")
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
