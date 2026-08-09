class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self.appointments = [dict(appointment) for appointment in appointments]
        self._appointment_ids = {
            appointment["id"] for appointment in self.appointments
        }
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

    def _screen(self, prompt=None, messages=None):
        prompt = self._next_prompt() if prompt is None else prompt
        choices = []
        if prompt == "Choose appointment":
            choices = [
                {"id": appointment["id"], "label": appointment["label"]}
                for appointment in self.appointments
            ]
        return {
            "prompt": prompt,
            "messages": list(messages or []),
            "choices": choices,
        }

    def _error(self, message):
        return self._screen(messages=["Error: " + message])

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return "no check-in session is active"
        if self.session["completed"]:
            return "the check-in session is already complete"
        return None

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")

        action_type = action.get("type")
        if action_type not in {
            "begin",
            "confirm_contact",
            "choose_appointment",
            "correct",
            "complete",
            "abandon",
        }:
            return self._error("unknown or missing action type")

        if action_type == "begin":
            if self.session["active"]:
                return self._error("a check-in session is already active")
            self.session = self._initial_session()
            self.session["active"] = True
            return self._screen(prompt="Confirm contact")

        if action_type == "abandon":
            if not self.session["active"]:
                return self._error("no check-in session is active")
            self.session = self._initial_session()
            return self._screen(
                prompt="Session abandoned",
                messages=["Check-in abandoned"],
            )

        state_error = self._require_active_incomplete()
        if state_error is not None:
            return self._error(state_error)

        if action_type == "confirm_contact":
            if "confirmed" not in action or type(action["confirmed"]) is not bool:
                return self._error("confirmed must be a boolean")
            self.session["contact_confirmed"] = action["confirmed"]
            prompt = (
                "Choose appointment"
                if action["confirmed"]
                else "Confirm contact"
            )
            return self._screen(prompt=prompt)

        if action_type == "choose_appointment":
            if (
                "appointment_id" not in action
                or type(action["appointment_id"]) is not str
            ):
                return self._error("appointment_id must be a string")
            if action["appointment_id"] not in self._appointment_ids:
                return self._error("appointment_id is not known")
            self.session["appointment_id"] = action["appointment_id"]
            return self._screen(prompt="Review and complete")

        if action_type == "correct":
            if "field" not in action or "value" not in action:
                return self._error("correction requires field and value")

            field = action["field"]
            value = action["value"]
            if field == "contact_confirmed":
                if type(value) is not bool:
                    return self._error(
                        "contact_confirmed correction must be a boolean"
                    )
            elif field == "appointment_id":
                if value is not None and type(value) is not str:
                    return self._error(
                        "appointment_id correction must be a string or None"
                    )
                if value is not None and value not in self._appointment_ids:
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

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires both an action source and screen sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
