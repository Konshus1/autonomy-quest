"""Console-oriented patient check-in component with injectable I/O.

Actions are structured dictionaries; output is deterministic screen
dictionaries. Input acquisition (``action_source.read_action()``) and
display (``screen_sink.display(screen)``) are injected, so unittest
fakes from the standard library are sufficient for testing.
"""


class CheckInConsole:
    """Drives a patient check-in session from structured actions."""

    PROMPT_CONTACT = "Confirm contact"
    PROMPT_APPOINTMENT = "Choose appointment"
    PROMPT_REVIEW = "Review and complete"
    PROMPT_COMPLETE = "Check-in complete"
    PROMPT_ABANDONED = "Session abandoned"
    PROMPT_ERROR = "Error"

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": entry["id"], "label": entry["label"]} for entry in appointments
        ]
        # A list (not a set) so membership tests never hash untrusted values.
        self._known_ids = [entry["id"] for entry in self._appointments]
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

    # ------------------------------------------------------------------
    # Screen construction

    def _screen(self, prompt, messages=None):
        if prompt == self.PROMPT_APPOINTMENT:
            choices = [dict(entry) for entry in self._appointments]
        else:
            choices = []
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error(self, reason):
        return self._screen(self.PROMPT_ERROR, ["Error: " + reason])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return self.PROMPT_CONTACT
        if self.session["appointment_id"] is None:
            return self.PROMPT_APPOINTMENT
        return self.PROMPT_REVIEW

    # ------------------------------------------------------------------
    # Action handling

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
            return self._error("unknown action type: %r" % (action.get("type"),))
        return handler(action)

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return "no active session"
        if self.session["completed"]:
            return "session already completed"
        return None

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("session already active")
        self.session = self._initial_session()
        self.session["active"] = True
        return self._screen(self.PROMPT_CONTACT)

    def _handle_confirm_contact(self, action):
        problem = self._require_active_incomplete()
        if problem is not None:
            return self._error(problem)
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        prompt = self.PROMPT_APPOINTMENT if confirmed else self.PROMPT_CONTACT
        return self._screen(prompt)

    def _handle_choose_appointment(self, action):
        problem = self._require_active_incomplete()
        if problem is not None:
            return self._error(problem)
        appointment_id = action.get("appointment_id")
        if appointment_id not in self._known_ids:
            return self._error("unknown appointment id: %r" % (appointment_id,))
        self.session["appointment_id"] = appointment_id
        return self._screen(self.PROMPT_REVIEW)

    def _handle_correct(self, action):
        problem = self._require_active_incomplete()
        if problem is not None:
            return self._error(problem)
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' requires a boolean value")
        elif field == "appointment_id":
            if value is not None and value not in self._known_ids:
                return self._error("unknown appointment id: %r" % (value,))
        else:
            return self._error("unknown correction field: %r" % (field,))
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        problem = self._require_active_incomplete()
        if problem is not None:
            return self._error(problem)
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(self.PROMPT_COMPLETE, ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = self._initial_session()
        return self._screen(self.PROMPT_ABANDONED, ["Check-in abandoned"])

    # ------------------------------------------------------------------
    # One console cycle

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink"
            )
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
