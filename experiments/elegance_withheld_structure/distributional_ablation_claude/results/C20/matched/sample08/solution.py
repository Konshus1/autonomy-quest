"""Console-oriented patient check-in component with replaceable I/O ports."""


def _initial_session():
    return {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }


class CheckInConsole:
    """Interprets structured check-in actions against authoritative session
    data and emits deterministic screen descriptions."""

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = _initial_session()

    # -- presentation ---------------------------------------------------

    def _screen(self, prompt, messages=None):
        if prompt == "Choose appointment":
            choices = [dict(a) for a in self._appointments]
        else:
            choices = []
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error(self, detail):
        return self._screen("Error", ["Error: " + detail])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    # -- action interpretation ------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        kind = action.get("type")
        if not isinstance(kind, str):
            return self._error("action 'type' must be a string")
        handlers = {
            "begin": self._handle_begin,
            "confirm_contact": self._handle_confirm_contact,
            "choose_appointment": self._handle_choose_appointment,
            "correct": self._handle_correct,
            "complete": self._handle_complete,
            "abandon": self._handle_abandon,
        }
        handler = handlers.get(kind)
        if handler is None:
            return self._error("unknown action type: " + kind)
        return handler(action)

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return "no active session"
        if self.session["completed"]:
            return "session already completed"
        return None

    def _known_appointment(self, appointment_id):
        return any(a["id"] == appointment_id for a in self._appointments)

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = _initial_session()
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
        prompt = "Choose appointment" if confirmed else "Confirm contact"
        return self._screen(prompt)

    def _handle_choose_appointment(self, action):
        problem = self._require_active_incomplete()
        if problem is not None:
            return self._error(problem)
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or not self._known_appointment(
            appointment_id
        ):
            return self._error("unknown appointment id: " + repr(appointment_id))
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
                return self._error("'contact_confirmed' value must be a boolean")
            self.session["contact_confirmed"] = value
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or not self._known_appointment(value)
            ):
                return self._error(
                    "'appointment_id' value must be a known id or None"
                )
            self.session["appointment_id"] = value
        else:
            return self._error("uncorrectable field: " + repr(field))
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
        return self._screen("Check-in complete", ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = _initial_session()
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # -- replaceable I/O ------------------------------------------------

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink"
            )
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
