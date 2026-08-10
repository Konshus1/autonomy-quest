"""Console-oriented patient check-in component.

State lives in a single public session dictionary; screens are built
deterministically from prompt and appointment data; input and output are
injected ports so unittest fakes can replace them.
"""


class CheckInConsole:
    _INITIAL_SESSION = {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._appointment_ids = {a["id"] for a in self._appointments}
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(self._INITIAL_SESSION)

    # -- screen construction -------------------------------------------

    def _screen(self, prompt, messages=()):
        choices = []
        if prompt == "Choose appointment":
            choices = [dict(a) for a in self._appointments]
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _error(self, reason):
        return self._screen("Error", ["Error: " + reason])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    # -- action handling -----------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        kind = action.get("type")
        if not isinstance(kind, str):
            return self._error("action type must be a string")
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
            return self._error("unknown action type: %r" % (kind,))
        return handler(action)

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = dict(self._INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _handle_confirm_contact(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("contact confirmation requires an active session")
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("confirmed must be a boolean")
        self.session["contact_confirmed"] = confirmed
        prompt = "Choose appointment" if confirmed else "Confirm contact"
        return self._screen(prompt)

    def _handle_choose_appointment(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("appointment choice requires an active session")
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._appointment_ids:
            return self._error("unknown appointment id: %r" % (appointment_id,))
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _handle_correct(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("correction requires an active session")
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("contact_confirmed correction requires a boolean value")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._appointment_ids
            ):
                return self._error("unknown appointment id: %r" % (value,))
        else:
            return self._error("unknown correction field: %r" % (field,))
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("completion requires an active session")
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session = dict(self._INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # -- mediated I/O ---------------------------------------------------

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires an action source and a screen sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
