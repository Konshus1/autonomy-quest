"""Console-oriented patient check-in component.

Exposes CheckInConsole, which consumes structured check-in actions,
maintains patient-facing session data, and emits deterministic screen
dictionaries. Input and output collaborators are injected so tests can
substitute unittest fakes using only the standard library.
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
_PROMPT_DONE = "Check-in complete"
_PROMPT_ABANDONED = "Session abandoned"
_PROMPT_BEGIN = "Begin check-in"


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._known_ids = {a["id"] for a in self._appointments}
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    # -- public API --------------------------------------------------

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
            return self._error("unknown action type")
        return handler(action)

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires an action source and a screen sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen

    # -- action handlers ---------------------------------------------

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("session already active")
        self.session = dict(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen(_PROMPT_CONFIRM)

    def _handle_confirm_contact(self, action):
        if not self._active_incomplete():
            return self._error("no active check-in session")
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("confirmed must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(_PROMPT_CHOOSE if confirmed else _PROMPT_CONFIRM)

    def _handle_choose_appointment(self, action):
        if not self._active_incomplete():
            return self._error("no active check-in session")
        appointment_id = action.get("appointment_id")
        if appointment_id not in self._known_ids:
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen(_PROMPT_REVIEW)

    def _handle_correct(self, action):
        if not self._active_incomplete():
            return self._error("no active check-in session")
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("contact_confirmed must be a boolean")
        elif field == "appointment_id":
            if value is not None and value not in self._known_ids:
                return self._error("unknown appointment id")
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        if not self._active_incomplete():
            return self._error("no active check-in session")
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(_PROMPT_DONE, ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session = dict(_INITIAL_SESSION)
        return self._screen(_PROMPT_ABANDONED, ["Check-in abandoned"])

    # -- helpers ------------------------------------------------------

    def _active_incomplete(self):
        return self.session["active"] and not self.session["completed"]

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return _PROMPT_CONFIRM
        if self.session["appointment_id"] is None:
            return _PROMPT_CHOOSE
        return _PROMPT_REVIEW

    def _current_prompt(self):
        if self.session["completed"]:
            return _PROMPT_DONE
        if not self.session["active"]:
            return _PROMPT_BEGIN
        return self._next_prompt()

    def _screen(self, prompt, messages=None):
        if prompt == _PROMPT_CHOOSE:
            choices = [
                {"id": a["id"], "label": a["label"]} for a in self._appointments
            ]
        else:
            choices = []
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error(self, text):
        screen = self._screen(self._current_prompt())
        screen["messages"].insert(0, "Error: " + text)
        return screen
