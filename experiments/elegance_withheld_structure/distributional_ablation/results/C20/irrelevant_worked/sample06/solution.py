class CheckInConsole:
    _INITIAL_SESSION = {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": appointment["id"], "label": appointment["label"]}
            for appointment in appointments
        ]
        self._appointment_ids = {
            appointment["id"] for appointment in self._appointments
        }
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = self._INITIAL_SESSION.copy()

    def _current_prompt(self):
        if self.session["completed"]:
            return "Check-in complete"
        if not self.session["active"]:
            return "Begin check-in"
        if self.session["contact_confirmed"] is not True:
            return "Confirm contact"
        if self.session["appointment_id"] is None:
            return "Choose appointment"
        return "Review and complete"

    def _screen(self, prompt=None, messages=None):
        prompt = self._current_prompt() if prompt is None else prompt
        choices = []
        if prompt == "Choose appointment":
            choices = [appointment.copy() for appointment in self._appointments]
        return {
            "prompt": prompt,
            "messages": [] if messages is None else list(messages),
            "choices": choices,
        }

    def _error(self, message):
        return self._screen(messages=["Error: " + message])

    def _require_active_incomplete(self):
        return self.session["active"] and not self.session["completed"]

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")

        action_type = action.get("type")
        valid_types = {
            "begin",
            "confirm_contact",
            "choose_appointment",
            "correct",
            "complete",
            "abandon",
        }
        if action_type not in valid_types:
            return self._error("unknown or missing action type")

        if action_type == "begin":
            if self.session["active"]:
                return self._error("a session is already active")
            self.session = self._INITIAL_SESSION.copy()
            self.session["active"] = True
            return self._screen(prompt="Confirm contact")

        if action_type == "abandon":
            if not self.session["active"]:
                return self._error("no active session to abandon")
            self.session = self._INITIAL_SESSION.copy()
            return self._screen(
                prompt="Session abandoned",
                messages=["Check-in abandoned"],
            )

        if not self._require_active_incomplete():
            return self._error("action requires an active, incomplete session")

        if action_type == "confirm_contact":
            confirmed = action.get("confirmed")
            if type(confirmed) is not bool:
                return self._error("confirmed must be a boolean")
            self.session["contact_confirmed"] = confirmed
            prompt = "Choose appointment" if confirmed else "Confirm contact"
            return self._screen(prompt=prompt)

        if action_type == "choose_appointment":
            appointment_id = action.get("appointment_id")
            if not isinstance(appointment_id, str):
                return self._error("appointment_id must be a string")
            if appointment_id not in self._appointment_ids:
                return self._error("appointment_id is not known")
            self.session["appointment_id"] = appointment_id
            return self._screen(prompt="Review and complete")

        if action_type == "correct":
            field = action.get("field")
            value = action.get("value")
            if field == "contact_confirmed":
                if type(value) is not bool:
                    return self._error(
                        "contact_confirmed correction must be a boolean"
                    )
            elif field == "appointment_id":
                if value is not None and (
                    not isinstance(value, str)
                    or value not in self._appointment_ids
                ):
                    return self._error(
                        "appointment_id correction must be a known id or None"
                    )
            else:
                return self._error("field is not correctable")
            self.session[field] = value
            return self._screen()

        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completion")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be selected before completion")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(
            prompt="Check-in complete",
            messages=["Checked in"],
        )

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action_source and screen_sink are required")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
