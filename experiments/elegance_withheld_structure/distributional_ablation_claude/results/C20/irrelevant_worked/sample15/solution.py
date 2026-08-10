"""Console check-in component driven by structured actions.

Input acquisition (``action_source.read_action()``) and output display
(``screen_sink.display(screen)``) are duck-typed ports replaceable by
plain unittest fakes.
"""


class CheckInConsole:
    """Maintains patient check-in session data and emits screen dicts."""

    _ACTION_TYPES = {
        "begin",
        "confirm_contact",
        "choose_appointment",
        "correct",
        "complete",
        "abandon",
    }

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

    # ------------------------------------------------------------------
    # Screen construction

    def _make_screen(self, prompt, messages=()):
        choices = (
            [dict(a) for a in self._appointments]
            if prompt == "Choose appointment"
            else []
        )
        return {"prompt": prompt, "messages": list(messages), "choices": choices}

    def _next_prompt(self):
        if self.session["completed"]:
            return "Check-in complete"
        if not self.session["active"]:
            return "Begin check-in"
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    def _error(self, reason):
        return self._make_screen(self._next_prompt(), ["Error: " + reason])

    # ------------------------------------------------------------------
    # Action handling

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")
        kind = action.get("type")
        if not isinstance(kind, str) or kind not in self._ACTION_TYPES:
            return self._error("unknown action type")
        return getattr(self, "_do_" + kind)(action)

    def _require_in_progress(self):
        if not self.session["active"]:
            return self._error("no active session")
        if self.session["completed"]:
            return self._error("session already completed")
        return None

    def _do_begin(self, action):
        if self.session["active"]:
            return self._error("session already active")
        self.session = self._initial_session()
        self.session["active"] = True
        return self._make_screen("Confirm contact")

    def _do_confirm_contact(self, action):
        error = self._require_in_progress()
        if error is not None:
            return error
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("confirmed must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._make_screen(
            "Choose appointment" if confirmed else "Confirm contact"
        )

    def _do_choose_appointment(self, action):
        error = self._require_in_progress()
        if error is not None:
            return error
        appointment_id = action.get("appointment_id")
        if (
            not isinstance(appointment_id, str)
            or appointment_id not in self._known_ids
        ):
            return self._error("unknown appointment id")
        self.session["appointment_id"] = appointment_id
        return self._make_screen("Review and complete")

    def _do_correct(self, action):
        error = self._require_in_progress()
        if error is not None:
            return error
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error(
                    "contact_confirmed correction requires a boolean"
                )
            self.session["contact_confirmed"] = value
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str) or value not in self._known_ids
            ):
                return self._error(
                    "appointment_id correction requires a known id or None"
                )
            self.session["appointment_id"] = value
        else:
            return self._error("unknown correction field")
        return self._make_screen(self._next_prompt())

    def _do_complete(self, action):
        error = self._require_in_progress()
        if error is not None:
            return error
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completion")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be chosen before completion")
        self.session["completed"] = True
        self.session["active"] = False
        return self._make_screen("Check-in complete", ["Checked in"])

    def _do_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active session")
        self.session = self._initial_session()
        return self._make_screen("Session abandoned", ["Check-in abandoned"])

    # ------------------------------------------------------------------
    # One interaction cycle through the replaceable ports

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink"
            )
        screen = self.handle(self.action_source.read_action())
        self.screen_sink.display(screen)
        return screen
