class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self.appointments = [dict(appointment) for appointment in appointments]
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
            choices = [
                {"id": appointment["id"], "label": appointment["label"]}
                for appointment in self.appointments
            ]
        return {
            "prompt": prompt,
            "messages": [] if messages is None else list(messages),
            "choices": choices,
        }

    def _error(self, explanation):
        return self._screen(messages=["Error: " + explanation])

    def _appointment_known(self, appointment_id):
        return any(
            appointment["id"] == appointment_id
            for appointment in self.appointments
        )

    def handle(self, action):
        try:
            if not isinstance(action, dict):
                return self._error("action must be a dictionary")

            action_type = action.get("type")
            valid_types = (
                "begin",
                "confirm_contact",
                "choose_appointment",
                "correct",
                "complete",
                "abandon",
            )
            if action_type not in valid_types:
                return self._error("unknown or missing action type")

            if action_type == "begin":
                if self.session["active"]:
                    return self._error("a session is already active")
                self.session = self._initial_session()
                self.session["active"] = True
                return self._screen(prompt="Confirm contact")

            if action_type == "abandon":
                if not self.session["active"]:
                    return self._error("no active session to abandon")
                self.session = self._initial_session()
                return self._screen(
                    prompt="Session abandoned",
                    messages=["Check-in abandoned"],
                )

            if not self.session["active"] or self.session["completed"]:
                return self._error("an active, incomplete session is required")

            if action_type == "confirm_contact":
                confirmed = action.get("confirmed")
                if not isinstance(confirmed, bool):
                    return self._error("confirmed must be a boolean")
                self.session["contact_confirmed"] = confirmed
                return self._screen(
                    prompt="Choose appointment" if confirmed else "Confirm contact"
                )

            if action_type == "choose_appointment":
                appointment_id = action.get("appointment_id")
                if not isinstance(appointment_id, str):
                    return self._error("appointment_id must be a string")
                if not self._appointment_known(appointment_id):
                    return self._error("appointment_id is not known")
                self.session["appointment_id"] = appointment_id
                return self._screen(prompt="Review and complete")

            if action_type == "correct":
                field = action.get("field")
                value = action.get("value")
                if field == "contact_confirmed":
                    if not isinstance(value, bool):
                        return self._error(
                            "contact_confirmed correction must be a boolean"
                        )
                elif field == "appointment_id":
                    if value is not None and not isinstance(value, str):
                        return self._error(
                            "appointment_id correction must be a known id or None"
                        )
                    if value is not None and not self._appointment_known(value):
                        return self._error("appointment_id is not known")
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
        except Exception:
            return self._error("malformed action")

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action source and screen sink are required")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
