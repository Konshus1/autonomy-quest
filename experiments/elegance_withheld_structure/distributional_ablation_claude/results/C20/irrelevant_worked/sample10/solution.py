"""Console-oriented patient check-in component with pluggable I/O adapters."""


class CheckInConsole:
    """Interprets structured check-in actions and emits screen descriptions.

    ``action_source`` must provide ``read_action() -> dict`` and
    ``screen_sink`` must provide ``display(screen)``; both are duck-typed so
    unittest fakes can stand in for them.
    """

    _PROMPT_IDLE = "Begin check-in"
    _PROMPT_CONTACT = "Confirm contact"
    _PROMPT_APPOINTMENT = "Choose appointment"
    _PROMPT_REVIEW = "Review and complete"
    _PROMPT_COMPLETE = "Check-in complete"
    _PROMPT_ABANDONED = "Session abandoned"

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": entry["id"], "label": entry["label"]} for entry in appointments
        ]
        self._known_ids = {entry["id"] for entry in self._appointments}
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = {}
        self._reset_session()

    # -- session state -----------------------------------------------------

    def _reset_session(self):
        # Mutate in place so external references to ``session`` stay valid.
        self.session.clear()
        self.session.update(
            active=False,
            contact_confirmed=None,
            appointment_id=None,
            completed=False,
        )

    def _next_prompt(self):
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
        return self._next_prompt()

    def _guard_active_incomplete(self):
        if not self.session["active"]:
            return "no active check-in session"
        if self.session["completed"]:
            return "check-in is already completed"
        return None

    # -- screen construction ----------------------------------------------

    def _screen(self, prompt, messages=()):
        choices = []
        if prompt == self._PROMPT_APPOINTMENT:
            choices = [dict(entry) for entry in self._appointments]
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _error(self, reason):
        return self._screen(self._current_prompt(), ["Error: " + reason])

    # -- action handling ---------------------------------------------------

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
            return self._error("unknown action type %r" % (action.get("type"),))
        return handler(action)

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self._reset_session()
        self.session["active"] = True
        return self._screen(self._PROMPT_CONTACT)

    def _handle_confirm_contact(self, action):
        problem = self._guard_active_incomplete()
        if problem is not None:
            return self._error(problem)
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        prompt = self._PROMPT_APPOINTMENT if confirmed else self._PROMPT_CONTACT
        return self._screen(prompt)

    def _handle_choose_appointment(self, action):
        problem = self._guard_active_incomplete()
        if problem is not None:
            return self._error(problem)
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._known_ids:
            return self._error("unknown appointment id %r" % (appointment_id,))
        self.session["appointment_id"] = appointment_id
        return self._screen(self._PROMPT_REVIEW)

    def _handle_correct(self, action):
        problem = self._guard_active_incomplete()
        if problem is not None:
            return self._error(problem)
        if "field" not in action or "value" not in action:
            return self._error("correction requires 'field' and 'value'")
        field = action["field"]
        value = action["value"]
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' value must be a boolean")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._known_ids
            ):
                return self._error("unknown appointment id %r" % (value,))
        else:
            return self._error("unknown correction field %r" % (field,))
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        problem = self._guard_active_incomplete()
        if problem is not None:
            return self._error(problem)
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(self._PROMPT_COMPLETE, ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active check-in session")
        self._reset_session()
        return self._screen(self._PROMPT_ABANDONED, ["Check-in abandoned"])

    # -- console loop ------------------------------------------------------

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError("run_once requires both an action source and a screen sink")
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
