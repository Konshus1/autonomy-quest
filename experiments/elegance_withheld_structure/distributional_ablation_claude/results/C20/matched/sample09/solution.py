"""Console-oriented patient check-in component.

Structured actions mutate an authoritative session dict; every action
produces a deterministic screen description {prompt, messages, choices}.
Input and output are duck-typed ports replaceable by unittest fakes.
"""

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
        self._known_ids = {a["id"] for a in self._appointments}
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)

    # -- presentation -----------------------------------------------------

    def _screen(self, prompt, messages=()):
        choices = []
        if prompt == "Choose appointment":
            choices = [dict(a) for a in self._appointments]
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

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

    def _error(self, reason):
        return self._screen(self._current_prompt(), ["Error: " + reason])

    # -- interpretation ---------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        handler = {
            "begin": self._begin,
            "confirm_contact": self._confirm_contact,
            "choose_appointment": self._choose_appointment,
            "correct": self._correct,
            "complete": self._complete,
            "abandon": self._abandon,
        }.get(action.get("type"))
        if handler is None:
            return self._error("unknown action type")
        return handler(action)

    def _begin(self, action):
        if self.session["active"]:
            return self._error("session already active")
        self.session.update(_INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _confirm_contact(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("no active session")
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("confirmed must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(self._next_prompt())

    def _choose_appointment(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("no active session")
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str) or appointment_id not in self._known_ids:
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _correct(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("no active session")
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("contact_confirmed must be a boolean")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._known_ids
            ):
                return self._error("appointment_id must be a known id or None")
        else:
            return self._error("unknown correction field")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _complete(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("no active session")
        if self.session["contact_confirmed"] is not True:
            return self._error("contact not confirmed")
        if self.session["appointment_id"] is None:
            return self._error("no appointment selected")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session.update(_INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # -- replaceable ports ------------------------------------------------

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action_source and screen_sink are required")
        screen = self.handle(self.action_source.read_action())
        self.screen_sink.display(screen)
        return screen
