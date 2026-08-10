"""Console-oriented patient check-in component.

Structured actions mutate a single session dictionary; every accepted or
rejected action yields a deterministic screen description.  Input and output
are injected ports so unittest fakes can replace them.
"""

_PROMPT_CONTACT = "Confirm contact"
_PROMPT_APPOINTMENT = "Choose appointment"
_PROMPT_REVIEW = "Review and complete"
_PROMPT_COMPLETE = "Check-in complete"
_PROMPT_ABANDONED = "Session abandoned"
_PROMPT_ERROR = "Invalid action"


def _initial_session():
    return {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = _initial_session()

    # -- screen construction -------------------------------------------------

    def _screen(self, prompt, messages=()):
        if prompt == _PROMPT_APPOINTMENT:
            choices = [{"id": a["id"], "label": a["label"]} for a in self._appointments]
        else:
            choices = []
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _error(self, reason):
        return self._screen(_PROMPT_ERROR, ["Error: " + reason])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return _PROMPT_CONTACT
        if self.session["appointment_id"] is None:
            return _PROMPT_APPOINTMENT
        return _PROMPT_REVIEW

    def _known_id(self, appointment_id):
        return any(a["id"] == appointment_id for a in self._appointments)

    # -- action handling -----------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        kind = action.get("type")
        if kind == "begin":
            return self._begin()
        if kind == "confirm_contact":
            return self._confirm_contact(action)
        if kind == "choose_appointment":
            return self._choose_appointment(action)
        if kind == "correct":
            return self._correct(action)
        if kind == "complete":
            return self._complete()
        if kind == "abandon":
            return self._abandon()
        return self._error("unknown action type: %r" % (kind,))

    def _require_open(self):
        if not self.session["active"]:
            return "no active session"
        if self.session["completed"]:
            return "session already completed"
        return None

    def _begin(self):
        if self.session["active"]:
            return self._error("session already active")
        self.session.update(_initial_session())
        self.session["active"] = True
        return self._screen(_PROMPT_CONTACT)

    def _confirm_contact(self, action):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("confirmed must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(_PROMPT_APPOINTMENT if confirmed else _PROMPT_CONTACT)

    def _choose_appointment(self, action):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or not self._known_id(appointment_id):
            return self._error("unknown appointment id: %r" % (appointment_id,))
        self.session["appointment_id"] = appointment_id
        return self._screen(_PROMPT_REVIEW)

    def _correct(self, action):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("contact_confirmed value must be a boolean")
        elif field == "appointment_id":
            if value is not None and not (
                isinstance(value, str) and self._known_id(value)
            ):
                return self._error("unknown appointment id: %r" % (value,))
        else:
            return self._error("unknown correction field: %r" % (field,))
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _complete(self):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completion")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completion")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(_PROMPT_COMPLETE, ["Checked in"])

    def _abandon(self):
        if not self.session["active"]:
            return self._error("no active session")
        self.session.update(_initial_session())
        return self._screen(_PROMPT_ABANDONED, ["Check-in abandoned"])

    # -- console loop step ---------------------------------------------------

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError("run_once requires an action_source and a screen_sink")
        screen = self.handle(self._action_source.read_action())
        self._screen_sink.display(screen)
        return screen
