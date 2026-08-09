class CheckInConsole:
    _INITIAL_SESSION = {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self.appointments = [
            {"id": item["id"], "label": item["label"]}
            for item in appointments
        ]
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = self._new_session()

    @classmethod
    def _new_session(cls):
        return dict(cls._INITIAL_SESSION)

    def _next_prompt(self, session=None):
        state = self.session if session is None else session
        if state["completed"]:
            return "Check-in complete"
        if not state["active"]:
            return "Begin check-in"
        if state["contact_confirmed"] is not True:
            return "Confirm contact"
        if state["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    def _screen(self, prompt=None, messages=None):
        resolved_prompt = prompt if prompt is not None else self._next_prompt()
        choices = []
        if resolved_prompt == "Choose appointment":
            choices = [dict(item) for item in self.appointments]
        return {
            "prompt": resolved_prompt,
            "messages": list(messages or []),
            "choices": choices,
        }

    def _error(self, explanation):
        return self._screen(messages=["Error: " + explanation])

    def _appointment_exists(self, appointment_id):
        return any(
            item["id"] == appointment_id for item in self.appointments
        )

    def handle(self, action):
        try:
            if not isinstance(action, dict):
                return self._error("action must be a dictionary")

            action_type = action.get("type")
            handlers = {
                "begin": self._begin,
                "confirm_contact": self._confirm_contact,
                "choose_appointment": self._choose_appointment,
                "correct": self._correct,
                "complete": self._complete,
                "abandon": self._abandon,
            }
            handler = handlers.get(action_type)
            if handler is None:
                return self._error("unknown or missing action type")
            return handler(action)
        except Exception:
            return self._error("malformed action")

    def _begin(self, action):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session = self._new_session()
        self.session["active"] = True
        return self._screen(prompt="Confirm contact")

    def _confirm_contact(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("contact confirmation requires an active session")
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error("confirmed must be a boolean")

        candidate = dict(self.session)
        candidate["contact_confirmed"] = confirmed
        self.session = candidate
        prompt = "Choose appointment" if confirmed else "Confirm contact"
        return self._screen(prompt=prompt)

    def _choose_appointment(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("appointment choice requires an active session")
        appointment_id = action.get("appointment_id")
        if not isinstance(appointment_id, str):
            return self._error("appointment_id must be a string")
        if not self._appointment_exists(appointment_id):
            return self._error("appointment_id is not known")

        candidate = dict(self.session)
        candidate["appointment_id"] = appointment_id
        self.session = candidate
        return self._screen(prompt="Review and complete")

    def _correct(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("correction requires an active incomplete session")

        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error("contact_confirmed correction must be boolean")
        elif field == "appointment_id":
            if value is not None and (
                not isinstance(value, str)
                or not self._appointment_exists(value)
            ):
                return self._error("appointment_id correction must be a known id or None")
        else:
            return self._error("field is not correctable")

        candidate = dict(self.session)
        candidate[field] = value
        self.session = candidate
        return self._screen(prompt=self._next_prompt(candidate))

    def _complete(self, action):
        if not self.session["active"] or self.session["completed"]:
            return self._error("completion requires an active incomplete session")
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completion")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be selected before completion")

        candidate = dict(self.session)
        candidate["completed"] = True
        candidate["active"] = False
        self.session = candidate
        return self._screen(
            prompt="Check-in complete",
            messages=["Checked in"],
        )

    def _abandon(self, action):
        if not self.session["active"]:
            return self._error("abandonment requires an active session")
        self.session = self._new_session()
        return self._screen(
            prompt="Session abandoned",
            messages=["Check-in abandoned"],
        )

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action source and screen sink are required")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
