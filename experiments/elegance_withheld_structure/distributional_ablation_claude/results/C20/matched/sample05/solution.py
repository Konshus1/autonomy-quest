"""Console-oriented patient check-in component with replaceable I/O ports."""


class CheckInConsole:
    """Interprets structured check-in actions against session data and
    emits deterministic screen descriptions."""

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._known_ids = [a["id"] for a in self._appointments]
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = self._initial_session()

    # ------------------------------------------------------------------
    # Session data

    @staticmethod
    def _initial_session():
        return {
            "active": False,
            "contact_confirmed": None,
            "appointment_id": None,
            "completed": False,
        }

    def _reset_session(self, active):
        # Mutate in place so external references to self.session stay valid.
        self.session.update(self._initial_session())
        self.session["active"] = active

    # ------------------------------------------------------------------
    # Screen construction

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == "Choose appointment":
            choices = [dict(a) for a in self._appointments]
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error(self, reason):
        return self._screen("Error", ["Error: " + reason])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    # ------------------------------------------------------------------
    # Action interpretation

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

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self._reset_session(active=True)
        return self._screen("Confirm contact")

    def _handle_confirm_contact(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("contact confirmation requires an active, incomplete session")
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen("Choose appointment" if confirmed else "Confirm contact")

    def _handle_choose_appointment(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("appointment choice requires an active, incomplete session")
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._known_ids:
            return self._error("unknown appointment id: %r" % (appointment_id,))
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _handle_correct(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("correction requires an active, incomplete session")
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("corrected 'contact_confirmed' must be a boolean")
            self.session["contact_confirmed"] = value
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._known_ids
            ):
                return self._error("corrected 'appointment_id' must be a known id or None")
            self.session["appointment_id"] = value
        else:
            return self._error("unknown correction field: %r" % (field,))
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("completion requires an active, incomplete session")
        if self.session["contact_confirmed"] is not True:
            return self._error("completion requires confirmed contact")
        if self.session["appointment_id"] is None:
            return self._error("completion requires a selected appointment")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("abandon requires an active session")
        self._reset_session(active=False)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # ------------------------------------------------------------------
    # Replaceable input/output

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires both an action_source and a screen_sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
