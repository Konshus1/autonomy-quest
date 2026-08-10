"""Console-oriented patient check-in component with injectable I/O."""

_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}


class CheckInConsole:
    """Drives a check-in flow from structured actions to screen dictionaries."""

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": entry["id"], "label": entry["label"]} for entry in appointments
        ]
        self._appointment_ids = {entry["id"] for entry in self._appointments}
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    # ------------------------------------------------------------------ screens

    def _screen(self, prompt, messages=()):
        choices = (
            [dict(entry) for entry in self._appointments]
            if prompt == "Choose appointment"
            else []
        )
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _error(self, reason):
        return self._screen(self._current_prompt(), ["Error: " + reason])

    def _current_prompt(self):
        session = self.session
        if not session["active"]:
            return "Check-in complete" if session["completed"] else "Begin check-in"
        if session["contact_confirmed"] is not True:
            return "Confirm contact"
        if session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    # ------------------------------------------------------------------ actions

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        action_type = action.get("type")
        if not isinstance(action_type, str) or action_type not in self._HANDLERS:
            return self._error("unknown action type")
        return self._HANDLERS[action_type](self, action)

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session.update(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _handle_confirm_contact(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("contact confirmation requires an active session")
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen("Choose appointment" if confirmed else "Confirm contact")

    def _handle_choose_appointment(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("appointment choice requires an active session")
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._appointment_ids:
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _handle_correct(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("correction requires an active session")
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' requires a boolean value")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._appointment_ids
            ):
                return self._error("'appointment_id' requires a known id or None")
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._current_prompt())

    def _handle_complete(self, action):
        session = self.session
        if not session["active"] or session["completed"]:
            return self._error("completion requires an active session")
        if session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        session["completed"] = True
        session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session.update(_INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    _HANDLERS = {
        "begin": _handle_begin,
        "confirm_contact": _handle_confirm_contact,
        "choose_appointment": _handle_choose_appointment,
        "correct": _handle_correct,
        "complete": _handle_complete,
        "abandon": _handle_abandon,
    }

    # ---------------------------------------------------------------------- I/O

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError("run_once requires an action source and a screen sink")
        screen = self.handle(self._action_source.read_action())
        self._screen_sink.display(screen)
        return screen
