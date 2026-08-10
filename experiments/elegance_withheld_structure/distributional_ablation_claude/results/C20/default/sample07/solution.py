"""Console-oriented patient check-in component with injectable I/O ports."""

_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self.appointments = [dict(a) for a in appointments]
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)
        self._known_ids = {a["id"] for a in self.appointments}

    # -- public API ------------------------------------------------------

    def handle(self, action):
        try:
            return self._dispatch(action)
        except Exception as exc:  # backstop: invalid input must never raise
            return self._error("malformed action ({})".format(exc))

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires an action_source and a screen_sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen

    # -- dispatch --------------------------------------------------------

    def _dispatch(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        handlers = {
            "begin": self._do_begin,
            "confirm_contact": self._do_confirm_contact,
            "choose_appointment": self._do_choose_appointment,
            "correct": self._do_correct,
            "complete": self._do_complete,
            "abandon": self._do_abandon,
        }
        handler = handlers.get(action.get("type"))
        if handler is None:
            return self._error("unknown action type {!r}".format(action.get("type")))
        return handler(action)

    # -- action handlers -------------------------------------------------

    def _do_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session.update(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _do_confirm_contact(self, action):
        blocked = self._require_active_incomplete()
        if blocked is not None:
            return blocked
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen("Choose appointment" if confirmed else "Confirm contact")

    def _do_choose_appointment(self, action):
        blocked = self._require_active_incomplete()
        if blocked is not None:
            return blocked
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._known_ids:
            return self._error("unknown appointment_id {!r}".format(appointment_id))
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _do_correct(self, action):
        blocked = self._require_active_incomplete()
        if blocked is not None:
            return blocked
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' correction needs a boolean value")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._known_ids
            ):
                return self._error("'appointment_id' correction needs a known id or None")
        else:
            return self._error("unknown correction field {!r}".format(field))
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _do_complete(self, action):
        blocked = self._require_active_incomplete()
        if blocked is not None:
            return blocked
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _do_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session.update(_INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # -- helpers ---------------------------------------------------------

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return self._error("no active session")
        if self.session["completed"]:
            return self._error("session is already completed")
        return None

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    def _current_prompt(self):
        if self.session["completed"]:
            return "Check-in complete"
        if not self.session["active"]:
            return "Begin check-in"
        return self._next_prompt()

    def _screen(self, prompt, messages=None):
        if prompt == "Choose appointment":
            choices = [{"id": a["id"], "label": a["label"]} for a in self.appointments]
        else:
            choices = []
        return {"prompt": prompt, "messages": list(messages or []), "choices": choices}

    def _error(self, detail):
        return self._screen(self._current_prompt(), ["Error: " + detail])
