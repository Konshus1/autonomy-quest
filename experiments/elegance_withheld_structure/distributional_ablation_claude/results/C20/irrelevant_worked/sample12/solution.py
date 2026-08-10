"""Console-oriented patient check-in component with replaceable I/O.

The console keeps one authoritative session dictionary, interprets
structured actions, and emits deterministic screen dictionaries of the
form {"prompt": str, "messages": list[str], "choices": list[dict]}.
Input acquisition and output display are injected objects exposing
read_action() and display(screen), so unittest fakes drop in directly.
"""

PROMPT_BEGIN = "Begin check-in"
PROMPT_CONTACT = "Confirm contact"
PROMPT_APPOINTMENT = "Choose appointment"
PROMPT_REVIEW = "Review and complete"
PROMPT_COMPLETE = "Check-in complete"
PROMPT_ABANDONED = "Session abandoned"


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [dict(entry) for entry in appointments]
        self._action_source = action_source
        self._screen_sink = screen_sink
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

    def _screen(self, prompt, messages=()):
        choices = []
        if prompt == PROMPT_APPOINTMENT:
            choices = [
                {"id": entry["id"], "label": entry["label"]}
                for entry in self._appointments
            ]
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _error(self, text):
        return self._screen(self._current_prompt(), [text])

    def _next_prompt(self):
        if self.session["contact_confirmed"] is not True:
            return PROMPT_CONTACT
        if self.session["appointment_id"] is None:
            return PROMPT_APPOINTMENT
        return PROMPT_REVIEW

    def _current_prompt(self):
        if self.session["completed"]:
            return PROMPT_COMPLETE
        if not self.session["active"]:
            return PROMPT_BEGIN
        return self._next_prompt()

    # ------------------------------------------------------------------
    # Action handling

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("Error: action must be a dictionary")
        kind = action.get("type")
        if kind == "begin":
            return self._handle_begin()
        if kind == "confirm_contact":
            return self._handle_confirm_contact(action)
        if kind == "choose_appointment":
            return self._handle_choose_appointment(action)
        if kind == "correct":
            return self._handle_correct(action)
        if kind == "complete":
            return self._handle_complete()
        if kind == "abandon":
            return self._handle_abandon()
        return self._error("Error: unknown action type")

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return self._error("Error: no active session")
        if self.session["completed"]:
            return self._error("Error: session already completed")
        return None

    def _handle_begin(self):
        if self.session["active"]:
            return self._error("Error: session already active")
        self.session = self._initial_session()
        self.session["active"] = True
        return self._screen(PROMPT_CONTACT)

    def _handle_confirm_contact(self, action):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("Error: confirmed must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(PROMPT_APPOINTMENT if confirmed else PROMPT_CONTACT)

    def _handle_choose_appointment(self, action):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or not any(
            entry["id"] == appointment_id for entry in self._appointments
        ):
            return self._error("Error: unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen(PROMPT_REVIEW)

    def _handle_correct(self, action):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("Error: contact_confirmed must be a boolean")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str)
                or not any(entry["id"] == value for entry in self._appointments)
            ):
                return self._error("Error: unknown appointment id")
        else:
            return self._error("Error: unknown correction field")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self):
        rejection = self._require_active_incomplete()
        if rejection is not None:
            return rejection
        if self.session["contact_confirmed"] is not True:
            return self._error("Error: contact not confirmed")
        if self.session["appointment_id"] is None:
            return self._error("Error: no appointment selected")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(PROMPT_COMPLETE, ["Checked in"])

    def _handle_abandon(self):
        if not self.session["active"]:
            return self._error("Error: no active session")
        self.session = self._initial_session()
        return self._screen(PROMPT_ABANDONED, ["Check-in abandoned"])

    # ------------------------------------------------------------------
    # Console loop step

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError("run_once requires an action source and a screen sink")
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
