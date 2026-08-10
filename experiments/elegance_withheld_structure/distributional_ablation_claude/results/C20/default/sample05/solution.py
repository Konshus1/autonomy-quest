"""Console-oriented patient check-in component with pluggable I/O.

Actions arrive as dictionaries, patient-facing state lives in a public
``session`` dictionary, and every interaction yields a deterministic screen
description (``prompt``/``messages``/``choices``).  The action source and
screen sink are injected so unittest fakes can replace them.
"""

_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}

_PROMPT_BEGIN = "Begin check-in"
_PROMPT_CONTACT = "Confirm contact"
_PROMPT_APPOINTMENT = "Choose appointment"
_PROMPT_REVIEW = "Review and complete"
_PROMPT_COMPLETE = "Check-in complete"
_PROMPT_ABANDONED = "Session abandoned"


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [dict(appointment) for appointment in appointments]
        self._known_ids = {appointment["id"] for appointment in self._appointments}
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    # -- public API ---------------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        handler = self._HANDLERS.get(action.get("type"))
        if handler is None:
            return self._error("unknown action type")
        return handler(self, action)

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError("run_once requires both an action source and a screen sink")
        screen = self.handle(self._action_source.read_action())
        self._screen_sink.display(screen)
        return screen

    # -- action handlers ----------------------------------------------------

    def _do_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session.update(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen(_PROMPT_CONTACT)

    def _do_confirm_contact(self, action):
        problem = self._session_problem()
        if problem is not None:
            return self._error(problem)
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(_PROMPT_APPOINTMENT if confirmed else _PROMPT_CONTACT)

    def _do_choose_appointment(self, action):
        problem = self._session_problem()
        if problem is not None:
            return self._error(problem)
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._known_ids:
            return self._error("'appointment_id' must be a known appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen(_PROMPT_REVIEW)

    def _do_correct(self, action):
        problem = self._session_problem()
        if problem is not None:
            return self._error(problem)
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("correction of 'contact_confirmed' requires a boolean value")
            self.session["contact_confirmed"] = value
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._known_ids
            ):
                return self._error("correction of 'appointment_id' requires a known id or None")
            self.session["appointment_id"] = value
        else:
            return self._error("unknown correction field")
        return self._screen(self._next_unmet_prompt())

    def _do_complete(self, action):
        problem = self._session_problem()
        if problem is not None:
            return self._error(problem)
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be selected before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(_PROMPT_COMPLETE, ["Checked in"])

    def _do_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session.update(_INITIAL_SESSION)
        return self._screen(_PROMPT_ABANDONED, ["Check-in abandoned"])

    # -- helpers ------------------------------------------------------------

    def _session_problem(self):
        if not self.session["active"]:
            return "no active session"
        if self.session["completed"]:
            return "session is already completed"
        return None

    def _next_unmet_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return _PROMPT_CONTACT
        if self.session["appointment_id"] is None:
            return _PROMPT_APPOINTMENT
        return _PROMPT_REVIEW

    def _current_prompt(self):
        if self.session["completed"]:
            return _PROMPT_COMPLETE
        if not self.session["active"]:
            return _PROMPT_BEGIN
        return self._next_unmet_prompt()

    def _screen(self, prompt, messages=None):
        if prompt == _PROMPT_APPOINTMENT:
            choices = [
                {"id": appointment["id"], "label": appointment["label"]}
                for appointment in self._appointments
            ]
        else:
            choices = []
        return {"prompt": prompt, "messages": list(messages or []), "choices": choices}

    def _error(self, detail):
        return self._screen(self._current_prompt(), ["Error: " + detail])

    _HANDLERS = {
        "begin": _do_begin,
        "confirm_contact": _do_confirm_contact,
        "choose_appointment": _do_choose_appointment,
        "correct": _do_correct,
        "complete": _do_complete,
        "abandon": _do_abandon,
    }
