"""Console-oriented patient check-in component with pluggable I/O."""


_PROMPT_BEGIN = "Begin check-in"
_PROMPT_CONTACT = "Confirm contact"
_PROMPT_APPOINTMENT = "Choose appointment"
_PROMPT_REVIEW = "Review and complete"
_PROMPT_COMPLETE = "Check-in complete"
_PROMPT_ABANDONED = "Session abandoned"


def _initial_session():
    return {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }


class CheckInConsole:
    """Drives a patient check-in flow from structured actions to screens.

    Screens are plain dictionaries with exactly the keys ``prompt``,
    ``messages`` and ``choices`` so that any sink capable of displaying a
    dictionary (including a unittest fake) can render them.
    """

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._appointment_ids = {a["id"] for a in self._appointments}
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = _initial_session()

    # -- screen construction ------------------------------------------------

    def _choices(self):
        return [{"id": a["id"], "label": a["label"]} for a in self._appointments]

    def _screen(self, prompt, messages=()):
        return {
            "prompt": prompt,
            "messages": list(messages),
            "choices": self._choices() if prompt == _PROMPT_APPOINTMENT else [],
        }

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return _PROMPT_CONTACT
        if self.session["appointment_id"] is None:
            return _PROMPT_APPOINTMENT
        return _PROMPT_REVIEW

    def _current_prompt(self):
        if not self.session["active"]:
            return _PROMPT_COMPLETE if self.session["completed"] else _PROMPT_BEGIN
        return self._next_prompt()

    def _error(self, reason):
        return self._screen(self._current_prompt(), ["Error: " + reason])

    # -- action handling ----------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        handler = self._HANDLERS.get(action.get("type"))
        if handler is None:
            return self._error("unknown action type")
        return handler(self, action)

    def _do_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session.update(_initial_session())
        self.session["active"] = True
        return self._screen(_PROMPT_CONTACT)

    def _require_open_session(self):
        if not self.session["active"] or self.session["completed"]:
            return self._error("no active check-in session")
        return None

    def _do_confirm_contact(self, action):
        error = self._require_open_session()
        if error is not None:
            return error
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(_PROMPT_APPOINTMENT if confirmed else _PROMPT_CONTACT)

    def _do_choose_appointment(self, action):
        error = self._require_open_session()
        if error is not None:
            return error
        appointment_id = action.get("appointment_id")
        if (
            not isinstance(appointment_id, str)
            or appointment_id not in self._appointment_ids
        ):
            return self._error("'appointment_id' must be a known appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen(_PROMPT_REVIEW)

    def _do_correct(self, action):
        error = self._require_open_session()
        if error is not None:
            return error
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error(
                    "correcting 'contact_confirmed' requires a boolean value"
                )
            self.session["contact_confirmed"] = value
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._appointment_ids
            ):
                return self._error(
                    "correcting 'appointment_id' requires a known id or None"
                )
            self.session["appointment_id"] = value
        else:
            return self._error("unknown correction field")
        return self._screen(self._next_prompt())

    def _do_complete(self, action):
        error = self._require_open_session()
        if error is not None:
            return error
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(_PROMPT_COMPLETE, ["Checked in"])

    def _do_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session.update(_initial_session())
        return self._screen(_PROMPT_ABANDONED, ["Check-in abandoned"])

    _HANDLERS = {
        "begin": _do_begin,
        "confirm_contact": _do_confirm_contact,
        "choose_appointment": _do_choose_appointment,
        "correct": _do_correct,
        "complete": _do_complete,
        "abandon": _do_abandon,
    }

    # -- console loop step --------------------------------------------------

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink"
            )
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
