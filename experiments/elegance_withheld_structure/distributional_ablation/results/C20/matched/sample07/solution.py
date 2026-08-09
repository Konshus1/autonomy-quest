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
        self._appointment_ids = {item["id"] for item in self.appointments}
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
        if prompt is None:
            prompt = self._current_prompt()

        choices = []
        if prompt == "Choose appointment":
            choices = [item.copy() for item in self.appointments]

        return {
            "prompt": prompt,
            "messages": [] if messages is None else list(messages),
            "choices": choices,
        }

    def _error(self, explanation):
        return self._screen(messages=["Error: " + explanation])

    def _reset_session(self):
        self.session.clear()
        self.session.update(self._INITIAL_SESSION)

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")

        action_type = action.get("type")
        if not isinstance(action_type, str):
            return self._error("action type must be a string")

        if action_type == "begin":
            if self.session["active"]:
                return self._error("a session is already active")
            self._reset_session()
            self.session["active"] = True
            return self._screen(prompt="Confirm contact")

        if action_type == "confirm_contact":
            if not self.session["active"] or self.session["completed"]:
                return self._error(
                    "contact confirmation requires an active, incomplete session"
                )
            confirmed = action.get("confirmed")
            if type(confirmed) is not bool:
                return self._error("confirmed must be a boolean")
            self.session["contact_confirmed"] = confirmed
            prompt = "Choose appointment" if confirmed else "Confirm contact"
            return self._screen(prompt=prompt)

        if action_type == "choose_appointment":
            if not self.session["active"] or self.session["completed"]:
                return self._error(
                    "appointment choice requires an active, incomplete session"
                )
            appointment_id = action.get("appointment_id")
            if not isinstance(appointment_id, str):
                return self._error("appointment_id must be a string")
            if appointment_id not in self._appointment_ids:
                return self._error("appointment_id is not known")
            self.session["appointment_id"] = appointment_id
            return self._screen(prompt="Review and complete")

        if action_type == "correct":
            if not self.session["active"] or self.session["completed"]:
                return self._error(
                    "correction requires an active, incomplete session"
                )

            field = action.get("field")
            value = action.get("value")

            if field == "contact_confirmed":
                if type(value) is not bool:
                    return self._error(
                        "contact_confirmed correction must be boolean"
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
                return self._error(
                    "field must be contact_confirmed or appointment_id"
                )

            self.session[field] = value
            return self._screen()

        if action_type == "complete":
            if not self.session["active"] or self.session["completed"]:
                return self._error(
                    "completion requires an active, incomplete session"
                )
            if self.session["contact_confirmed"] is not True:
                return self._error(
                    "contact must be confirmed before completion"
                )
            if self.session["appointment_id"] is None:
                return self._error(
                    "an appointment must be selected before completion"
                )

            self.session["completed"] = True
            self.session["active"] = False
            return self._screen(
                prompt="Check-in complete",
                messages=["Checked in"],
            )

        if action_type == "abandon":
            if not self.session["active"]:
                return self._error("abandon requires an active session")
            self._reset_session()
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
