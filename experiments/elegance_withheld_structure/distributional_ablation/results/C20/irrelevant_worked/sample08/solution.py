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
        self.session.update(changes)

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

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == "Choose appointment":
            choices = [dict(item) for item in self._appointments]
        return {
            "prompt": prompt,
            "messages": list(messages or []),
            "choices": choices,
        }

    def _error(self, message):
        return self._screen(self._current_prompt(), ["Error: " + message])

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")

        try:
            action_type = action.get("type")

            if action_type == "begin":
                if self.session["active"]:
                    return self._error("a session is already active")
                self.session.clear()
                self.session.update(self._INITIAL_SESSION)
                self._commit(active=True)
                return self._screen("Confirm contact")

            if action_type == "confirm_contact":
                if not self.session["active"] or self.session["completed"]:
                    return self._error("contact confirmation requires an active, incomplete session")
                confirmed = action.get("confirmed")
                if type(confirmed) is not bool:
                    return self._error("confirmed must be a boolean")
                self._commit(contact_confirmed=confirmed)
                prompt = "Choose appointment" if confirmed else "Confirm contact"
                return self._screen(prompt)

            if action_type == "choose_appointment":
                if not self.session["active"] or self.session["completed"]:
                    return self._error("appointment selection requires an active, incomplete session")
                appointment_id = action.get("appointment_id")
                if not isinstance(appointment_id, str):
                    return self._error("appointment_id must be a string")
                if appointment_id not in self._appointment_ids:
                    return self._error("appointment_id is not known")
                self._commit(appointment_id=appointment_id)
                return self._screen("Review and complete")

            if action_type == "correct":
                if not self.session["active"] or self.session["completed"]:
                    return self._error("correction requires an active, incomplete session")
                field = action.get("field")
                value = action.get("value")

                if field == "contact_confirmed":
                    if type(value) is not bool:
                        return self._error("contact_confirmed correction must be boolean")
                elif field == "appointment_id":
                    if value is not None and (
                        not isinstance(value, str) or value not in self._appointment_ids
                    ):
                        return self._error("appointment_id correction must be a known id or None")
                else:
                    return self._error("field must be contact_confirmed or appointment_id")

                self._commit(**{field: value})
                return self._screen(self._current_prompt())

            if action_type == "complete":
                if not self.session["active"] or self.session["completed"]:
                    return self._error("completion requires an active, incomplete session")
                if self.session["contact_confirmed"] is not True:
                    return self._error("contact must be confirmed before completion")
                if self.session["appointment_id"] is None:
                    return self._error("an appointment must be selected before completion")
                self._commit(active=False, completed=True)
                return self._screen("Check-in complete", ["Checked in"])

            if action_type == "abandon":
                if not self.session["active"]:
                    return self._error("abandon requires an active session")
                self.session.clear()
                self.session.update(self._INITIAL_SESSION)
                return self._screen("Session abandoned", ["Check-in abandoned"])

            return self._error("unknown or missing action type")
        except Exception:
            return self._error("malformed action")

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires both an action source and a screen sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
