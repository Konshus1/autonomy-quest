"""Console-oriented patient check-in component with replaceable I/O ports.

The console keeps an authoritative session dictionary, interprets structured
check-in actions, and answers every action with a deterministic screen
description ({"prompt", "messages", "choices"}).  Input acquisition and
output display are injected objects so tests can substitute fakes.
"""


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self._known_ids = {a["id"] for a in self._appointments}
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

    # -- presentation -----------------------------------------------------

    def _screen(self, prompt, messages):
        if prompt == "Choose appointment":
            choices = [
                {"id": a["id"], "label": a["label"]}
                for a in self._appointments
            ]
        else:
            choices = []
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _current_prompt(self):
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
        return self._screen(self._current_prompt(), ["Error: " + reason])

    # -- action handling --------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        action_type = action.get("type")

        if action_type == "begin":
            return self._handle_begin()
        if action_type == "confirm_contact":
            return self._handle_confirm_contact(action)
        if action_type == "choose_appointment":
            return self._handle_choose_appointment(action)
        if action_type == "correct":
            return self._handle_correct(action)
        if action_type == "complete":
            return self._handle_complete()
        if action_type == "abandon":
            return self._handle_abandon()
        return self._error("unknown action type")

    def _require_open_session(self):
        if not self.session["active"]:
            return "no active session"
        if self.session["completed"]:
            return "session already completed"
        return None

    def _handle_begin(self):
        if self.session["active"]:
            return self._error("session already active")
        self.session = self._initial_session()
        self.session["active"] = True
        return self._screen("Confirm contact", [])

    def _handle_confirm_contact(self, action):
        problem = self._require_open_session()
        if problem:
            return self._error(problem)
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("confirmed must be a boolean")
        self.session["contact_confirmed"] = confirmed
        prompt = "Choose appointment" if confirmed else "Confirm contact"
        return self._screen(prompt, [])

    def _handle_choose_appointment(self, action):
        problem = self._require_open_session()
        if problem:
            return self._error(problem)
        appointment_id = action.get("appointment_id")
        if not (isinstance(appointment_id, str)
                and appointment_id in self._known_ids):
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete", [])

    def _handle_correct(self, action):
        problem = self._require_open_session()
        if problem:
            return self._error(problem)
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("contact_confirmed must be a boolean")
            self.session["contact_confirmed"] = value
        elif field == "appointment_id":
            if not (value is None or (isinstance(value, str)
                                      and value in self._known_ids)):
                return self._error("appointment_id must be a known id or None")
            self.session["appointment_id"] = value
        else:
            return self._error("unknown correction field")
        return self._screen(self._current_prompt(), [])

    def _handle_complete(self):
        problem = self._require_open_session()
        if problem:
            return self._error(problem)
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completing")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _handle_abandon(self):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = self._initial_session()
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # -- mediated I/O ------------------------------------------------------

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action_source and screen_sink are required")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
