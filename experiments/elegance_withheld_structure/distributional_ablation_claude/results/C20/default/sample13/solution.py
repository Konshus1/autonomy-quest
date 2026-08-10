"""Console-oriented patient check-in component with pluggable I/O.

Exposes CheckInConsole, which consumes structured check-in actions,
maintains patient-facing session state, and emits deterministic screen
dictionaries. Input acquisition and output display are duck-typed
collaborators (read_action() / display(screen)) so unittest fakes built
from the standard library can replace them.
"""

_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}


class CheckInConsole:
    """Drives one patient check-in session via structured actions."""

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
    def _screen(self, prompt, messages=()):
        if prompt == "Choose appointment":
            choices = [dict(entry) for entry in self._appointments]
        else:
            choices = []
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _error(self, reason):
        return self._screen("Error", ["Error: " + reason])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    # ------------------------------------------------------------------
    # Action handling
    # ------------------------------------------------------------------
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
            return self._error("a session is already active")
        self.session = dict(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _handle_confirm_contact(self, action):
        problem = self._require_active_incomplete()
        if problem is not None:
            return self._error(problem)
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen("Choose appointment" if confirmed else "Confirm contact")

    def _handle_choose_appointment(self, action):
        problem = self._require_active_incomplete()
        if problem is not None:
            return self._error(problem)
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._known_ids:
            return self._error("unknown appointment id: %r" % (appointment_id,))
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _handle_correct(self, action):
        problem = self._require_active_incomplete()
        if problem is not None:
            return self._error(problem)
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error(
                    "correction of 'contact_confirmed' requires a boolean value"
                )
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._known_ids
            ):
                return self._error(
                    "correction of 'appointment_id' requires a known id or None"
                )
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
            return self._error("an appointment must be selected before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = dict(_INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # ------------------------------------------------------------------
    # Console loop step
    # ------------------------------------------------------------------
    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink"
            )
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
