"""Console-oriented patient check-in component with replaceable I/O ports."""

PROMPT_BEGIN = "Begin check-in"
PROMPT_CONFIRM = "Confirm contact"
PROMPT_CHOOSE = "Choose appointment"
PROMPT_REVIEW = "Review and complete"
PROMPT_DONE = "Check-in complete"
PROMPT_ABANDONED = "Session abandoned"

_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}


class CheckInConsole:
    """Interprets structured check-in actions against authoritative session
    data and emits deterministic screen descriptions."""

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    # -- screen construction (pure function of state) -------------------

    def _screen(self, prompt, messages=()):
        choices = []
        if prompt == PROMPT_CHOOSE:
            choices = [dict(a) for a in self._appointments]
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _state_prompt(self):
        if self.session["completed"]:
            return PROMPT_DONE
        if not self.session["active"]:
            return PROMPT_BEGIN
        if self.session["contact_confirmed"] is not True:
            return PROMPT_CONFIRM
        if self.session["appointment_id"] is None:
            return PROMPT_CHOOSE
        return PROMPT_REVIEW

    def _error(self, reason):
        return self._screen(self._state_prompt(), ["Error: " + reason])

    # -- action interpretation ------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        kind = action.get("type")
        if not isinstance(kind, str):
            return self._error("action 'type' must be a string")
        handlers = {
            "begin": self._do_begin,
            "confirm_contact": self._do_confirm_contact,
            "choose_appointment": self._do_choose_appointment,
            "correct": self._do_correct,
            "complete": self._do_complete,
            "abandon": self._do_abandon,
        }
        handler = handlers.get(kind)
        if handler is None:
            return self._error("unknown action type: %r" % (kind,))
        return handler(action)

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return self._error("no active session")
        if self.session["completed"]:
            return self._error("session already completed")
        return None

    def _do_begin(self, action):
        if self.session["active"]:
            return self._error("session already active")
        self.session.update(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen(PROMPT_CONFIRM)

    def _do_confirm_contact(self, action):
        error = self._require_active_incomplete()
        if error is not None:
            return error
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(PROMPT_CHOOSE if confirmed else PROMPT_CONFIRM)

    def _do_choose_appointment(self, action):
        error = self._require_active_incomplete()
        if error is not None:
            return error
        appointment_id = action.get("appointment_id")
        if appointment_id not in [a["id"] for a in self._appointments]:
            return self._error("unknown appointment id: %r" % (appointment_id,))
        self.session["appointment_id"] = appointment_id
        return self._screen(PROMPT_REVIEW)

    def _do_correct(self, action):
        error = self._require_active_incomplete()
        if error is not None:
            return error
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error(
                    "correcting 'contact_confirmed' requires a boolean value"
                )
        elif field == "appointment_id":
            if value is not None and value not in [
                a["id"] for a in self._appointments
            ]:
                return self._error("unknown appointment id: %r" % (value,))
        else:
            return self._error("unknown correction field: %r" % (field,))
        self.session[field] = value
        return self._screen(self._state_prompt())

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
        return self._screen(PROMPT_DONE, ["Checked in"])

    def _do_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session.update(_INITIAL_SESSION)
        return self._screen(PROMPT_ABANDONED, ["Check-in abandoned"])

    # -- replaceable I/O ports ------------------------------------------

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink"
            )
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
