"""Console-oriented patient check-in component with replaceable I/O ports."""


class CheckInConsole:
    """Interprets structured check-in actions against patient session data
    and emits deterministic screen descriptions.

    Screens are dictionaries with exactly the keys ``prompt`` (str),
    ``messages`` (list of str), and ``choices`` (list of
    ``{"id": str, "label": str}``).
    """

    _ACTION_TYPES = (
        "begin",
        "confirm_contact",
        "choose_appointment",
        "correct",
        "complete",
        "abandon",
    )

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": a["id"], "label": a["label"]} for a in appointments
        ]
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = {
            "active": False,
            "contact_confirmed": None,
            "appointment_id": None,
            "completed": False,
        }

    # ------------------------------------------------------------------
    # Presentation
    # ------------------------------------------------------------------

    def _screen(self, prompt, messages=()):
        if prompt == "Choose appointment":
            choices = [
                {"id": a["id"], "label": a["label"]}
                for a in self._appointments
            ]
        else:
            choices = []
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
        return self._screen(self._next_prompt(), ["Error: " + reason])

    # ------------------------------------------------------------------
    # Action handling
    # ------------------------------------------------------------------

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary.")
        kind = action.get("type")
        if kind not in self._ACTION_TYPES:
            return self._error("unknown action type.")
        return getattr(self, "_do_" + kind)(action)

    def _in_progress(self):
        return self.session["active"] and not self.session["completed"]

    def _known_id(self, appointment_id):
        return any(a["id"] == appointment_id for a in self._appointments)

    def _do_begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active.")
        self.session.update(
            active=True,
            contact_confirmed=None,
            appointment_id=None,
            completed=False,
        )
        return self._screen("Confirm contact")

    def _do_confirm_contact(self, action):
        if not self._in_progress():
            return self._error("no active check-in session.")
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("'confirmed' must be a boolean.")
        self.session["contact_confirmed"] = confirmed
        return self._screen(
            "Choose appointment" if confirmed else "Confirm contact"
        )

    def _do_choose_appointment(self, action):
        if not self._in_progress():
            return self._error("no active check-in session.")
        appointment_id = action.get("appointment_id")
        if not self._known_id(appointment_id):
            return self._error("unknown appointment id.")
        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _do_correct(self, action):
        if not self._in_progress():
            return self._error("no active check-in session.")
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("'contact_confirmed' must be a boolean.")
        elif field == "appointment_id":
            if value is not None and not self._known_id(value):
                return self._error("unknown appointment id.")
        else:
            return self._error("unknown correction field.")
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _do_complete(self, action):
        if not self._in_progress():
            return self._error("no active check-in session.")
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completing.")
        if self.session["appointment_id"] is None:
            return self._error(
                "an appointment must be chosen before completing."
            )
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _do_abandon(self, action):
        if not self.session["active"]:
            return self._error("no active check-in session.")
        self.session.update(
            active=False,
            contact_confirmed=None,
            appointment_id=None,
            completed=False,
        )
        return self._screen("Session abandoned", ["Check-in abandoned"])

    # ------------------------------------------------------------------
    # Console loop step
    # ------------------------------------------------------------------

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError(
                "run_once requires both an action source and a screen sink."
            )
        screen = self.handle(self.action_source.read_action())
        self.screen_sink.display(screen)
        return screen
