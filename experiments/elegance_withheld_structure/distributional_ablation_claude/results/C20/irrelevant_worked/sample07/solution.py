"""Console-oriented patient check-in component with replaceable I/O ports."""

PROMPT_CONFIRM = "Confirm contact"
PROMPT_CHOOSE = "Choose appointment"
PROMPT_REVIEW = "Review and complete"
PROMPT_DONE = "Check-in complete"
PROMPT_ABANDONED = "Session abandoned"
PROMPT_ERROR = "Error"


class CheckInConsole:
    """Accepts structured check-in actions and emits deterministic screens.

    ``action_source`` must provide ``read_action()`` and ``screen_sink`` must
    provide ``display(screen)``; both are only needed for ``run_once()``.
    """

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [dict(a) for a in appointments]
        self._known_ids = {a["id"] for a in self._appointments}
        self._action_source = action_source
        self._screen_sink = screen_sink
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
        choices = []
        if prompt == PROMPT_CHOOSE:
            choices = [{"id": a["id"], "label": a["label"]}
                       for a in self._appointments]
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _error(self, text):
        return self._screen(PROMPT_ERROR, ["Error: " + text])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return PROMPT_CONFIRM
        if self.session["appointment_id"] is None:
            return PROMPT_CHOOSE
        return PROMPT_REVIEW

    # -- guards --------------------------------------------------------------

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return self._error("no active session")
        if self.session["completed"]:
            return self._error("session is already completed")
        return None

    # -- action handlers -----------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        handler = self._HANDLERS.get(action.get("type"))
        if handler is None:
            return self._error(
                "unknown action type: %r" % (action.get("type"),))
        return handler(self, action)

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = self._initial_session()
        self.session["active"] = True
        return self._screen(PROMPT_CONFIRM)

    def _handle_confirm_contact(self, action):
        guard = self._require_active_incomplete()
        if guard is not None:
            return guard
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(PROMPT_CHOOSE if confirmed else PROMPT_CONFIRM)

    def _handle_choose_appointment(self, action):
        guard = self._require_active_incomplete()
        if guard is not None:
            return guard
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) \
                or appointment_id not in self._known_ids:
            return self._error(
                "unknown appointment id: %r" % (appointment_id,))
        self.session["appointment_id"] = appointment_id
        return self._screen(PROMPT_REVIEW)

    def _handle_correct(self, action):
        guard = self._require_active_incomplete()
        if guard is not None:
            return guard
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error(
                    "'contact_confirmed' correction requires a boolean value")
        elif field == "appointment_id":
            if value is not None and (not isinstance(value, str)
                                      or value not in self._known_ids):
                return self._error(
                    "'appointment_id' correction requires a known id or None")
        else:
            return self._error("unknown correction field: %r" % (field,))
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        guard = self._require_active_incomplete()
        if guard is not None:
            return guard
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(PROMPT_DONE, ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = self._initial_session()
        return self._screen(PROMPT_ABANDONED, ["Check-in abandoned"])

    _HANDLERS = {
        "begin": _handle_begin,
        "confirm_contact": _handle_confirm_contact,
        "choose_appointment": _handle_choose_appointment,
        "correct": _handle_correct,
        "complete": _handle_complete,
        "abandon": _handle_abandon,
    }

    # -- console loop step ---------------------------------------------------

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action_source and a screen_sink")
        screen = self.handle(self._action_source.read_action())
        self._screen_sink.display(screen)
        return screen
