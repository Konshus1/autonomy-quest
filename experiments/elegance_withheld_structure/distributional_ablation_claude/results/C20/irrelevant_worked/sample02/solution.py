"""Console-oriented patient check-in component with replaceable I/O.

The console holds one public ``session`` dictionary, interprets structured
check-in actions, and emits deterministic screen dictionaries of the form
``{"prompt": str, "messages": list[str], "choices": list[dict]}``.
Input and output are duck-typed collaborators (``read_action()`` and
``display(screen)``) so unittest fakes can replace them.
"""


class CheckInConsole:
    _PROMPT_CONTACT = "Confirm contact"
    _PROMPT_APPOINTMENT = "Choose appointment"
    _PROMPT_REVIEW = "Review and complete"
    _PROMPT_COMPLETE = "Check-in complete"
    _PROMPT_ABANDONED = "Session abandoned"
    _PROMPT_IDLE = "Begin check-in"

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [dict(appointment) for appointment in appointments]
        self._known_ids = {appointment["id"] for appointment in self._appointments}
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

    # ------------------------------------------------------------------
    # Action handling

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        action_type = action.get("type")
        if not isinstance(action_type, str):
            return self._error("action requires a string 'type'")
        handler = self._HANDLERS.get(action_type)
        if handler is None:
            return self._error("unknown action type %r" % action_type)
        return handler(self, action)

    def _do_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = self._initial_session()
        self.session["active"] = True
        return self._screen(self._PROMPT_CONTACT)

    def _do_confirm_contact(self, action):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        if confirmed:
            return self._screen(self._PROMPT_APPOINTMENT)
        return self._screen(self._PROMPT_CONTACT)

    def _do_choose_appointment(self, action):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._known_ids:
            return self._error("'appointment_id' must be a known appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen(self._PROMPT_REVIEW)

    def _do_correct(self, action):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("correction of 'contact_confirmed' needs a boolean value")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._known_ids
            ):
                return self._error("correction of 'appointment_id' needs a known id or None")
        else:
            return self._error("'field' must be 'contact_confirmed' or 'appointment_id'")
        self.session[field] = value
        return self._screen(self._next_unmet_prompt())

    def _do_complete(self, action):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(self._PROMPT_COMPLETE, ["Checked in"])

    def _do_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session = self._initial_session()
        return self._screen(self._PROMPT_ABANDONED, ["Check-in abandoned"])

    _HANDLERS = {
        "begin": _do_begin,
        "confirm_contact": _do_confirm_contact,
        "choose_appointment": _do_choose_appointment,
        "correct": _do_correct,
        "complete": _do_complete,
        "abandon": _do_abandon,
    }

    # ------------------------------------------------------------------
    # Screen construction

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return self._error("no active session")
        if self.session["completed"]:
            return self._error("session is already completed")
        return None

    def _next_unmet_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return self._PROMPT_CONTACT
        if self.session["appointment_id"] is None:
            return self._PROMPT_APPOINTMENT
        return self._PROMPT_REVIEW

    def _current_prompt(self):
        if self.session["completed"]:
            return self._PROMPT_COMPLETE
        if not self.session["active"]:
            return self._PROMPT_IDLE
        return self._next_unmet_prompt()

    def _screen(self, prompt, messages=None):
        if prompt == self._PROMPT_APPOINTMENT:
            choices = [
                {"id": appointment["id"], "label": appointment["label"]}
                for appointment in self._appointments
            ]
        else:
            choices = []
        return {
            "prompt": prompt,
            "messages": list(messages) if messages is not None else [],
            "choices": choices,
        }

    def _error(self, reason):
        return self._screen(self._current_prompt(), ["Error: " + reason])

    # ------------------------------------------------------------------
    # One console interaction

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires both an action_source and a screen_sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
