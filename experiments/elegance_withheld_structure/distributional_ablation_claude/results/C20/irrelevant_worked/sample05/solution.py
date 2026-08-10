"""Console-oriented patient check-in component with replaceable I/O adapters."""

PROMPT_CONFIRM = "Confirm contact"
PROMPT_CHOOSE = "Choose appointment"
PROMPT_REVIEW = "Review and complete"
PROMPT_DONE = "Check-in complete"
PROMPT_ABANDONED = "Session abandoned"

_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._appointment_ids = {a["id"] for a in self._appointments}
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        handler = self._HANDLERS.get(action.get("type"))
        if handler is None:
            return self._error("unknown or missing action type")
        return handler(self, action)

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink"
            )
        screen = self.handle(self.action_source.read_action())
        self.screen_sink.display(screen)
        return screen

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = dict(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen(PROMPT_CONFIRM)

    def _handle_confirm_contact(self, action):
        err = self._require_active_incomplete()
        if err is not None:
            return err
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(PROMPT_CHOOSE if confirmed else PROMPT_CONFIRM)

    def _handle_choose_appointment(self, action):
        err = self._require_active_incomplete()
        if err is not None:
            return err
        appointment_id = action.get("appointment_id")
        if (
            not isinstance(appointment_id, str)
            or appointment_id not in self._appointment_ids
        ):
            return self._error("'appointment_id' must be a known appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen(PROMPT_REVIEW)

    def _handle_correct(self, action):
        err = self._require_active_incomplete()
        if err is not None:
            return err
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("corrected 'contact_confirmed' must be a boolean")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._appointment_ids
            ):
                return self._error(
                    "corrected 'appointment_id' must be a known id or None"
                )
        else:
            return self._error("'field' must be 'contact_confirmed' or 'appointment_id'")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        err = self._require_active_incomplete()
        if err is not None:
            return err
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(PROMPT_DONE, ["Checked in"])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session to abandon")
        self.session = dict(_INITIAL_SESSION)
        return self._screen(PROMPT_ABANDONED, ["Check-in abandoned"])

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return self._error("no active session")
        if self.session["completed"]:
            return self._error("session is already completed")
        return None

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return PROMPT_CONFIRM
        if self.session["appointment_id"] is None:
            return PROMPT_CHOOSE
        return PROMPT_REVIEW

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == PROMPT_CHOOSE:
            choices = [
                {"id": a["id"], "label": a["label"]} for a in self._appointments
            ]
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error(self, detail):
        return {"prompt": "Error", "messages": ["Error: " + detail], "choices": []}

    _HANDLERS = {
        "begin": _handle_begin,
        "confirm_contact": _handle_confirm_contact,
        "choose_appointment": _handle_choose_appointment,
        "correct": _handle_correct,
        "complete": _handle_complete,
        "abandon": _handle_abandon,
    }
