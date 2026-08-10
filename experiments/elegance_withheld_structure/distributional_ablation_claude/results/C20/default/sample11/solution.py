"""Console-oriented patient check-in component with injectable I/O."""

_PROMPT_CONTACT = "Confirm contact"
_PROMPT_APPOINTMENT = "Choose appointment"
_PROMPT_REVIEW = "Review and complete"
_PROMPT_COMPLETE = "Check-in complete"
_PROMPT_ABANDONED = "Session abandoned"
_PROMPT_ERROR = "Error"


def _initial_session():
    return {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }


class CheckInConsole:
    """Drives a patient check-in session from structured actions.

    ``action_source`` must provide ``read_action()`` and ``screen_sink``
    must provide ``display(screen)``; both are only required by
    ``run_once`` and may be plain unittest fakes.
    """

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = _initial_session()

    # -- screen construction -------------------------------------------

    def _screen(self, prompt, messages=None):
        if prompt == _PROMPT_APPOINTMENT:
            choices = [dict(a) for a in self._appointments]
        else:
            choices = []
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error(self, reason):
        return self._screen(_PROMPT_ERROR, ["Error: " + reason])

    def _next_unmet_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return _PROMPT_CONTACT
        if self.session["appointment_id"] is None:
            return _PROMPT_APPOINTMENT
        return _PROMPT_REVIEW

    # -- validation helpers --------------------------------------------

    def _known_id(self, appointment_id):
        return any(a["id"] == appointment_id for a in self._appointments)

    def _open_session_error(self):
        if not self.session["active"]:
            return "no active session"
        if self.session["completed"]:
            return "session already completed"
        return None

    # -- action handling -----------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dict")
        handler = self._HANDLERS.get(action.get("type"))
        if handler is None:
            return self._error("unknown action type")
        return handler(self, action)

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("session already active")
        self.session.update(_initial_session())
        self.session["active"] = True
        return self._screen(_PROMPT_CONTACT)

    def _handle_confirm_contact(self, action):
        reason = self._open_session_error()
        if reason is not None:
            return self._error(reason)
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(
            _PROMPT_APPOINTMENT if confirmed else _PROMPT_CONTACT
        )

    def _handle_choose_appointment(self, action):
        reason = self._open_session_error()
        if reason is not None:
            return self._error(reason)
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or not self._known_id(
            appointment_id
        ):
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen(_PROMPT_REVIEW)

    def _handle_correct(self, action):
        reason = self._open_session_error()
        if reason is not None:
            return self._error(reason)
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error(
                    "'contact_confirmed' correction requires a boolean value"
                )
        elif field == "appointment_id":
            if value is not None and not (
                isinstance(value, str) and self._known_id(value)
            ):
                return self._error(
                    "'appointment_id' correction requires a known id or None"
                )
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._next_unmet_prompt())

    def _handle_complete(self, action):
        reason = self._open_session_error()
        if reason is not None:
            return self._error(reason)
        if (
            self.session["contact_confirmed"] is not True
            or self.session["appointment_id"] is None
        ):
            return self._error(
                "contact confirmation and an appointment are required"
            )
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(_PROMPT_COMPLETE, ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session.update(_initial_session())
        return self._screen(_PROMPT_ABANDONED, ["Check-in abandoned"])

    _HANDLERS = {
        "begin": _handle_begin,
        "confirm_contact": _handle_confirm_contact,
        "choose_appointment": _handle_choose_appointment,
        "correct": _handle_correct,
        "complete": _handle_complete,
        "abandon": _handle_abandon,
    }

    # -- I/O loop step --------------------------------------------------

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action_source and a screen_sink"
            )
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
