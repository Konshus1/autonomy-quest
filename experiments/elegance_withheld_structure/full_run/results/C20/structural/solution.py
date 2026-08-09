class CheckInConsole:
    _INITIAL_SESSION = {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }

    def __init__(self, appointments, action_source=None, screen_sink=None):
        copied = []
        seen_ids = set()
        for appointment in appointments:
            if type(appointment) is not dict:
                raise TypeError("appointments must contain dictionaries")
            appointment_id = appointment.get("id")
            label = appointment.get("label")
            if type(appointment_id) is not str or type(label) is not str:
                raise TypeError("appointment id and label must be strings")
            if appointment_id in seen_ids:
                raise ValueError("appointment ids must be unique")
            seen_ids.add(appointment_id)
            copied.append({"id": appointment_id, "label": label})

        self.appointments = copied
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = self._new_session()
        self._appointment_ids = seen_ids

    @classmethod
    def _new_session(cls):
        return dict(cls._INITIAL_SESSION)

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
            choices = [dict(appointment) for appointment in self.appointments]
        return {
            "prompt": prompt,
            "messages": list(messages or []),
            "choices": choices,
        }

    def _error(self, explanation):
        return self._screen(
            self._current_prompt(),
            ["Error: " + explanation],
        )

    def _requires_active_incomplete(self):
        return self.session["active"] and not self.session["completed"]

    def handle(self, action):
        if type(action) is not dict:
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
            updated = self._new_session()
            updated["active"] = True
            self.session = updated
            return self._screen("Confirm contact")

        if action_type == "abandon":
            if not self.session["active"]:
                return self._error("no active session to abandon")
            self.session = self._new_session()
            return self._screen(
                "Session abandoned",
                ["Check-in abandoned"],
            )

        if not self._requires_active_incomplete():
            return self._error("an active, incomplete session is required")

        if action_type == "confirm_contact":
            confirmed = action.get("confirmed")
            if type(confirmed) is not bool:
                return self._error("confirmed must be a boolean")
            updated = dict(self.session)
            updated["contact_confirmed"] = confirmed
            self.session = updated
            prompt = "Choose appointment" if confirmed else "Confirm contact"
            return self._screen(prompt)

        if action_type == "choose_appointment":
            appointment_id = action.get("appointment_id")
            if type(appointment_id) is not str:
                return self._error("appointment_id must be a string")
            if appointment_id not in self._appointment_ids:
                return self._error("appointment_id is not known")
            updated = dict(self.session)
            updated["appointment_id"] = appointment_id
            self.session = updated
            return self._screen("Review and complete")

        if action_type == "correct":
            field = action.get("field")
            if field == "contact_confirmed":
                value = action.get("value")
                if type(value) is not bool:
                    return self._error(
                        "contact_confirmed correction must be a boolean"
                    )
            elif field == "appointment_id":
                value = action.get("value")
                if value is not None and (
                    type(value) is not str or value not in self._appointment_ids
                ):
                    return self._error(
                        "appointment_id correction must be a known id or None"
                    )
            else:
                return self._error("field is not correctable")

            updated = dict(self.session)
            updated[field] = value
            self.session = updated
            return self._screen(self._current_prompt())

        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completion")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be selected before completion")

        updated = dict(self.session)
        updated["completed"] = True
        updated["active"] = False
        self.session = updated
        return self._screen("Check-in complete", ["Checked in"])

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action source and screen sink are required")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
