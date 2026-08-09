class CheckInConsole:
    _INITIAL_SESSION = {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": item["id"], "label": item["label"]}
            for item in appointments
        ]
        self._appointment_ids = {item["id"] for item in self._appointments}
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(self._INITIAL_SESSION)

    def _commit(self, **changes):
        updated = dict(self.session)
        updated.update(changes)
        self.session.clear()
        self.session.update(updated)

    def _reset(self):
        self.session.clear()
        self.session.update(self._INITIAL_SESSION)

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

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == "Choose appointment":
            choices = [dict(item) for item in self._appointments]
        return {
            "prompt": prompt,
            "messages": [] if messages is None else list(messages),
            "choices": choices,
        }

    def _error(self, message):
        return self._screen(self._next_prompt(), ["Error: " + message])

    def _requires_active_incomplete(self):
        return self.session["active"] and not self.session["completed"]

    def handle(self, action):
        try:
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
                    return self._error("a session is already active")
                self._reset()
                self._commit(active=True)
                return self._screen("Confirm contact")

            if action_type == "abandon":
                if not self.session["active"]:
                    return self._error("no active session to abandon")
                self._reset()
                return self._screen(
                    "Session abandoned", ["Check-in abandoned"]
                )

            if not self._requires_active_incomplete():
                return self._error("an active, incomplete session is required")

            if action_type == "confirm_contact":
                confirmed = action.get("confirmed")
                if type(confirmed) is not bool:
                    return self._error("confirmed must be a boolean")
                self._commit(contact_confirmed=confirmed)
                prompt = "Choose appointment" if confirmed else "Confirm contact"
                return self._screen(prompt)

            if action_type == "choose_appointment":
                appointment_id = action.get("appointment_id")
                if not isinstance(appointment_id, str):
                    return self._error("appointment_id must be a string")
                if appointment_id not in self._appointment_ids:
                    return self._error("appointment_id is not known")
                self._commit(appointment_id=appointment_id)
                return self._screen("Review and complete")

            if action_type == "correct":
                field = action.get("field")
                value = action.get("value")

                if field == "contact_confirmed":
                    if type(value) is not bool:
                        return self._error(
                            "contact_confirmed correction must be a boolean"
                        )
                    self._commit(contact_confirmed=value)
                elif field == "appointment_id":
                    if value is not None and not isinstance(value, str):
                        return self._error(
                            "appointment_id correction must be a string or None"
                        )
                    if value is not None and value not in self._appointment_ids:
                        return self._error("appointment_id is not known")
                    self._commit(appointment_id=value)
                else:
                    return self._error("field is not correctable")

                return self._screen(self._next_prompt())

            if self.session["contact_confirmed"] is not True:
                return self._error("contact must be confirmed before completion")
            if self.session["appointment_id"] is None:
                return self._error("an appointment must be selected before completion")

            self._commit(active=False, completed=True)
            return self._screen("Check-in complete", ["Checked in"])
        except Exception:
            return self._error("malformed action")

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action source and screen sink are required")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
