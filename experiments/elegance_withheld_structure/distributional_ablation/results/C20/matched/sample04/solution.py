class CheckInConsole:
    _INITIAL_SESSION = {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self.appointments = [dict(appointment) for appointment in appointments]
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(self._INITIAL_SESSION)
        self._appointment_ids = {
            appointment["id"] for appointment in self.appointments
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
        resolved_prompt = prompt if prompt is not None else self._next_prompt()
        choices = []
        if resolved_prompt == "Choose appointment":
            choices = [
                {"id": appointment["id"], "label": appointment["label"]}
                for appointment in self.appointments
            ]
        return {
            "prompt": resolved_prompt,
            "messages": list(messages or []),
            "choices": choices,
        }

    def _error(self, explanation):
        return self._screen(messages=["Error: " + explanation])

    def _active_incomplete_error(self):
        if not self.session["active"]:
            return "no check-in session is active"
        if self.session["completed"]:
            return "the check-in session is already complete"
        return None

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")

        action_type = action.get("type")
        if not isinstance(action_type, str):
            return self._error("action type must be a string")

        if action_type == "begin":
            if self.session["active"]:
                return self._error("a check-in session is already active")
            self.session.update(self._INITIAL_SESSION)
            self.session["active"] = True
            return self._screen(prompt="Confirm contact")

        if action_type == "confirm_contact":
            state_error = self._active_incomplete_error()
            if state_error:
                return self._error(state_error)
            confirmed = action.get("confirmed")
            if not isinstance(confirmed, bool):
                return self._error("confirmed must be a boolean")
            self.session["contact_confirmed"] = confirmed
            prompt = "Choose appointment" if confirmed else "Confirm contact"
            return self._screen(prompt=prompt)

        if action_type == "choose_appointment":
            state_error = self._active_incomplete_error()
            if state_error:
                return self._error(state_error)
            appointment_id = action.get("appointment_id")
            if not isinstance(appointment_id, str):
                return self._error("appointment_id must be a string")
            if appointment_id not in self._appointment_ids:
                return self._error("appointment_id is not known")
            self.session["appointment_id"] = appointment_id
            return self._screen(prompt="Review and complete")

        if action_type == "correct":
            state_error = self._active_incomplete_error()
            if state_error:
                return self._error(state_error)
            field = action.get("field")
            if field == "contact_confirmed":
                value = action.get("value")
                if not isinstance(value, bool):
                    return self._error(
                        "contact_confirmed correction must be a boolean"
                    )
            elif field == "appointment_id":
                value = action.get("value")
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

        if action_type == "complete":
            state_error = self._active_incomplete_error()
            if state_error:
                return self._error(state_error)
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

        if action_type == "abandon":
            if not self.session["active"]:
                return self._error("no check-in session is active")
            self.session.update(self._INITIAL_SESSION)
            return self._screen(
                prompt="Session abandoned",
                messages=["Check-in abandoned"],
            )

        return self._error("unknown action type")

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action source and screen sink are required")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
