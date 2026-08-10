"""Console-oriented patient check-in component with injectable I/O.

The console consumes structured action dictionaries and emits deterministic
screen dictionaries. Input acquisition (``action_source.read_action()``) and
output display (``screen_sink.display(screen)``) are constructor-injected so
tests can substitute plain fake objects using only the standard library.
"""

_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}

_PROMPT_CONFIRM = "Confirm contact"
_PROMPT_CHOOSE = "Choose appointment"
_PROMPT_REVIEW = "Review and complete"
_PROMPT_COMPLETE = "Check-in complete"
_PROMPT_ABANDONED = "Session abandoned"
_PROMPT_ERROR = "Error"


class CheckInConsole:
    """Drives a patient check-in session over structured actions and screens."""

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": entry["id"], "label": entry["label"]} for entry in appointments
        ]
        self._known_ids = {entry["id"] for entry in self._appointments}
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    # ------------------------------------------------------------------
    # Screen construction
    # ------------------------------------------------------------------

    def _screen(self, prompt, messages=None):
        if prompt == _PROMPT_CHOOSE:
            choices = [dict(entry) for entry in self._appointments]
        else:
            choices = []
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error(self, reason):
        return self._screen(_PROMPT_ERROR, ["Error: " + reason])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return _PROMPT_CONFIRM
        if self.session["appointment_id"] is None:
            return _PROMPT_CHOOSE
        return _PROMPT_REVIEW

    def _in_progress(self):
        return self.session["active"] and not self.session["completed"]

    # ------------------------------------------------------------------
    # Action handling
    # ------------------------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        action_type = action.get("type")
        handlers = {
            "begin": self._handle_begin,
            "confirm_contact": self._handle_confirm_contact,
            "choose_appointment": self._handle_choose_appointment,
            "correct": self._handle_correct,
            "complete": self._handle_complete,
            "abandon": self._handle_abandon,
        }
        if not isinstance(action_type, str) or action_type not in handlers:
            return self._error("unknown action type")
        return handlers[action_type](action)

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = dict(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen(_PROMPT_CONFIRM)

    def _handle_confirm_contact(self, action):
        if not self._in_progress():
            return self._error("no active session")
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(_PROMPT_CHOOSE if confirmed else _PROMPT_CONFIRM)

    def _handle_choose_appointment(self, action):
        if not self._in_progress():
            return self._error("no active session")
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._known_ids:
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen(_PROMPT_REVIEW)

    def _handle_correct(self, action):
        if not self._in_progress():
            return self._error("no active session")
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' requires a boolean value")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._known_ids
            ):
                return self._error("'appointment_id' requires a known id or None")
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        if not self._in_progress():
            return self._error("no active session")
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(_PROMPT_COMPLETE, ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = dict(_INITIAL_SESSION)
        return self._screen(_PROMPT_ABANDONED, ["Check-in abandoned"])

    # ------------------------------------------------------------------
    # Driving loop step
    # ------------------------------------------------------------------

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError("run_once requires both an action_source and a screen_sink")
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
