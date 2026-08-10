"""Console-oriented patient check-in component with replaceable I/O ports."""


class CheckInConsole:
    """Maintains check-in session data and emits deterministic screen dicts."""

    _ACTION_TYPES = ("begin", "confirm_contact", "choose_appointment",
                     "correct", "complete", "abandon")

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": appt["id"], "label": appt["label"]} for appt in appointments
        ]
        self.action_source = action_source
        self.screen_sink = screen_sink
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
    # ------------------------------------------------------------------

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == "Choose appointment":
            choices = [dict(appt) for appt in self._appointments]
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _current_prompt(self):
        session = self.session
        if session["completed"]:
            return "Check-in complete"
        if not session["active"]:
            return "Begin check-in"
        if session["contact_confirmed"] is not True:
            return "Confirm contact"
        if session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    def _error(self, reason):
        return self._screen(self._current_prompt(), ["Error: " + reason])

    # ------------------------------------------------------------------
    # Action handling
    # ------------------------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        kind = action.get("type")
        if kind not in self._ACTION_TYPES:
            return self._error("unknown action type")
        return getattr(self, "_do_" + kind)(action)

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return "no active session"
        if self.session["completed"]:
            return "session already completed"
        return None

    def _known_id(self, appointment_id):
        return any(a["id"] == appointment_id for a in self._appointments)

    def _do_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = self._initial_session()
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _do_confirm_contact(self, action):
        problem = self._require_active_incomplete()
        if problem:
            return self._error(problem)
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("confirmed must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen("Choose appointment" if confirmed else "Confirm contact")

    def _do_choose_appointment(self, action):
        problem = self._require_active_incomplete()
        if problem:
            return self._error(problem)
        appointment_id = action.get("appointment_id")
        if not self._known_id(appointment_id):
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _do_correct(self, action):
        problem = self._require_active_incomplete()
        if problem:
            return self._error(problem)
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("contact_confirmed correction requires a boolean")
        elif field == "appointment_id":
            if value is not None and not self._known_id(value):
                return self._error("unknown appointment id")
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._current_prompt())

    def _do_complete(self, action):
        problem = self._require_active_incomplete()
        if problem:
            return self._error(problem)
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _do_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = self._initial_session()
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # ------------------------------------------------------------------
    # Mediated I/O
    # ------------------------------------------------------------------

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires an action source and a screen sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
