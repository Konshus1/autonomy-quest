"""Console-oriented patient check-in component.

A single session dictionary holds authoritative check-in state, every
action yields a deterministic screen description (prompt, messages,
choices), and input/output are pluggable ports so unittest fakes can
drive the console.
"""

_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}

_ACTION_TYPES = {
    "begin", "confirm_contact", "choose_appointment",
    "correct", "complete", "abandon",
}


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    # -- screen building --------------------------------------------------

    def _choices_for(self, prompt):
        if prompt == "Choose appointment":
            return [dict(a) for a in self._appointments]
        return []

    def _screen(self, prompt, messages=None):
        return {
            "prompt": prompt,
            "messages": list(messages or []),
            "choices": self._choices_for(prompt),
        }

    def _next_prompt(self):
        s = self.session
        if s["completed"]:
            return "Check-in complete"
        if not s["active"]:
            return "Begin check-in"
        if s["contact_confirmed"] is not True:
            return "Confirm contact"
        if s["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    def _error(self, reason):
        return self._screen(self._next_prompt(), ["Error: " + reason])

    def _known_id(self, appointment_id):
        return any(a["id"] == appointment_id for a in self._appointments)

    # -- action interpretation --------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        kind = action.get("type")
        if kind not in _ACTION_TYPES:
            return self._error("unknown action type")
        return getattr(self, "_do_" + kind)(action)

    def _require_open(self):
        if not self.session["active"] or self.session["completed"]:
            return "no active session"
        return None

    def _do_begin(self, action):
        if self.session["active"]:
            return self._error("session already active")
        self.session.update(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _do_confirm_contact(self, action):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(
            "Choose appointment" if confirmed else "Confirm contact"
        )

    def _do_choose_appointment(self, action):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or not self._known_id(appointment_id):
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _do_correct(self, action):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' must be a boolean")
        elif field == "appointment_id":
            if value is not None and not (
                isinstance(value, str) and self._known_id(value)
            ):
                return self._error("'appointment_id' must be a known id or None")
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _do_complete(self, action):
        problem = self._require_open()
        if problem:
            return self._error(problem)
        if self.session["contact_confirmed"] is not True:
            return self._error("contact not confirmed")
        if self.session["appointment_id"] is None:
            return self._error("no appointment chosen")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _do_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session.update(_INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # -- port-driven loop step --------------------------------------------

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action_source and screen_sink are required")
        screen = self.handle(self.action_source.read_action())
        self.screen_sink.display(screen)
        return screen
