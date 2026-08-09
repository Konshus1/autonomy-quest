class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        copied = []
        seen_ids = set()
        for appointment in appointments:
            if not isinstance(appointment, dict):
                raise TypeError("appointments must contain dictionaries")
            appointment_id = appointment.get("id")
            label = appointment.get("label")
            if not isinstance(appointment_id, str) or not isinstance(label, str):
                raise ValueError("appointment id and label must be strings")
            if appointment_id in seen_ids:
                raise ValueError("appointment ids must be unique")
            seen_ids.add(appointment_id)
            copied.append({"id": appointment_id, "label": label})

        self._appointments = copied
        self._appointment_ids = seen_ids
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = {
            "active": False,
            "contact_confirmed": None,
            "appointment_id": None,
            "completed": False,
        }

    def _reset_session(self):
        self.session.clear()
        self.session.update({
            "active": False,
            "contact_confirmed": None,
            "appointment_id": None,
            "completed": False,
        })

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
            choices = [dict(appointment) for appointment in self._appointments]
        return {
            "prompt": prompt,
            "messages": list(messages or []),
            "choices": choices,
        }

    def _error(self, explanation):
        return self._screen(
            self._next_prompt(),
            ["Error: " + explanation],
        )

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")

        action_type = action.get("type")

        if action_type == "begin":
            if self.session["active"]:
                return self._error("a session is already active")
            self._reset_session()
            self.session["active"] = True
            return self._screen("Confirm contact")

        if action_type == "confirm_contact":
            if not self.session["active"] or self.session["completed"]:
                return self._error("contact confirmation requires an active, incomplete session")
            confirmed = action.get("confirmed")
            if not isinstance(confirmed, bool):
                return self._error("confirmed must be a boolean")
            self.session["contact_confirmed"] = confirmed
            prompt = "Choose appointment" if confirmed else "Confirm contact"
            return self._screen(prompt)

        if action_type == "choose_appointment":
            if not self.session["active"] or self.session["completed"]:
                return self._error("appointment choice requires an active, incomplete session")
            appointment_id = action.get("appointment_id")
            if not isinstance(appointment_id, str):
                return self._error("appointment_id must be a string")
            if appointment_id not in self._appointment_ids:
                return self._error("appointment_id is not known")
            self.session["appointment_id"] = appointment_id
            return self._screen("Review and complete")

        if action_type == "correct":
            if not self.session["active"] or self.session["completed"]:
                return self._error("correction requires an active, incomplete session")

            field = action.get("field")
            value = action.get("value")

            if field == "contact_confirmed":
                if not isinstance(value, bool):
                    return self._error("contact_confirmed correction value must be a boolean")
                self.session["contact_confirmed"] = value
            elif field == "appointment_id":
                if value is not None and (
                    not isinstance(value, str) or value not in self._appointment_ids
                ):
                    return self._error("appointment_id correction value must be a known id or None")
                self.session["appointment_id"] = value
            else:
                return self._error("field must be contact_confirmed or appointment_id")

            return self._screen(self._next_prompt())

        if action_type == "complete":
            if not self.session["active"] or self.session["completed"]:
                return self._error("completion requires an active, incomplete session")
            if self.session["contact_confirmed"] is not True:
                return self._error("contact must be confirmed before completion")
            if self.session["appointment_id"] is None:
                return self._error("an appointment must be selected before completion")
            self.session["completed"] = True
            self.session["active"] = False
            return self._screen("Check-in complete", ["Checked in"])

        if action_type == "abandon":
            if not self.session["active"]:
                return self._error("abandonment requires an active session")
            self._reset_session()
            return self._screen("Session abandoned", ["Check-in abandoned"])

        return self._error("unknown or missing action type")

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires both an action source and a screen sink")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
