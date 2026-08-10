"""Console-oriented patient check-in component with replaceable I/O ports.

State lives in a single public ``session`` dictionary; screens are derived
deterministically from that state; input and output go through injected
ports so tests can substitute fakes.
"""

_PROMPT_IDLE = "Begin check-in"
_PROMPT_CONTACT = "Confirm contact"
_PROMPT_APPOINTMENT = "Choose appointment"
_PROMPT_REVIEW = "Review and complete"
_PROMPT_COMPLETE = "Check-in complete"
_PROMPT_ABANDONED = "Session abandoned"


class CheckInConsole:
    """Interpret structured check-in actions and emit screen descriptions."""

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [dict(appointment) for appointment in appointments]
        self._appointment_ids = {appointment["id"] for appointment in self._appointments}
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = {
            "active": False,
            "contact_confirmed": None,
            "appointment_id": None,
            "completed": False,
        }

    # -- public entry points -------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        kind = action.get("type")
        if kind == "begin":
            return self._handle_begin()
        if kind == "confirm_contact":
            return self._handle_confirm_contact(action)
        if kind == "choose_appointment":
            return self._handle_choose_appointment(action)
        if kind == "correct":
            return self._handle_correct(action)
        if kind == "complete":
            return self._handle_complete()
        if kind == "abandon":
            return self._handle_abandon()
        return self._error("unknown action type: {!r}".format(kind))

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires both an action source and a screen sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen

    # -- action handlers -----------------------------------------------------

    def _handle_begin(self):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session.update(
            active=True, contact_confirmed=None, appointment_id=None, completed=False
        )
        return self._screen(_PROMPT_CONTACT)

    def _handle_confirm_contact(self, action):
        if not self._in_progress():
            return self._error("no active check-in session")
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(_PROMPT_APPOINTMENT if confirmed else _PROMPT_CONTACT)

    def _handle_choose_appointment(self, action):
        if not self._in_progress():
            return self._error("no active check-in session")
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._appointment_ids:
            return self._error("unknown appointment id: {!r}".format(appointment_id))
        self.session["appointment_id"] = appointment_id
        return self._screen(_PROMPT_REVIEW)

    def _handle_correct(self, action):
        if not self._in_progress():
            return self._error("no active check-in session")
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' correction requires a boolean value")
            self.session["contact_confirmed"] = value
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._appointment_ids
            ):
                return self._error(
                    "'appointment_id' correction requires a known id or None"
                )
            self.session["appointment_id"] = value
        else:
            return self._error("unknown correction field: {!r}".format(field))
        return self._screen(self._next_prompt())

    def _handle_complete(self):
        if not self._in_progress():
            return self._error("no active check-in session")
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be selected before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(_PROMPT_COMPLETE, ["Checked in"])

    def _handle_abandon(self):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session.update(
            active=False, contact_confirmed=None, appointment_id=None, completed=False
        )
        return self._screen(_PROMPT_ABANDONED, ["Check-in abandoned"])

    # -- screen construction ------------------------------------------------

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == _PROMPT_APPOINTMENT:
            choices = [
                {"id": appointment["id"], "label": appointment["label"]}
                for appointment in self._appointments
            ]
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error(self, reason):
        return self._screen(self._current_prompt(), ["Error: " + reason])

    # -- state queries -------------------------------------------------------

    def _in_progress(self):
        return self.session["active"] and not self.session["completed"]

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return _PROMPT_CONTACT
        if self.session["appointment_id"] is None:
            return _PROMPT_APPOINTMENT
        return _PROMPT_REVIEW

    def _current_prompt(self):
        if self.session["completed"]:
            return _PROMPT_COMPLETE
        if not self.session["active"]:
            return _PROMPT_IDLE
        return self._next_prompt()
