class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        copied = []
        seen = set()
        for appointment in appointments:
            if not isinstance(appointment, dict):
                raise ValueError("appointments must contain dictionaries")
            appointment_id = appointment.get("id")
            label = appointment.get("label")
            if (
                not isinstance(appointment_id, str)
                or not isinstance(label, str)
                or appointment_id in seen
            ):
                raise ValueError("appointments require unique string ids and string labels")
            seen.add(appointment_id)
            copied.append({"id": appointment_id, "label": label})

        self._appointments = copied
        self._appointment_ids = seen
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = {}
        self._reset_session()

    def _reset_session(self):
        self.session.clear()
        self.session.update({
            "active": False,
            "contact_confirmed": None,
            "appointment_id": None,
            "completed": False,
        })

    def _screen(self, prompt, messages=None):
        choices = []
        if prompt == "Choose appointment":
            choices = [dict(appointment) for appointment in self._appointments]
        return {
            "prompt": prompt,
            "messages": list(messages or []),
            "choices": choices,
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

    def _error(self, message):
        return self._screen(self._current_prompt(), ["Error: " + message])

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
            self._reset_session()
            self.session["active"] = True
            return self._screen("Confirm contact")

        if action_type == "abandon":
            if not self.session["active"]:
                return self._error("no active session to abandon")
            self._reset_session()
            return self._screen("Session abandoned", ["Check-in abandoned"])

        if not self._require_active_incomplete():
            return self._error("an active, incomplete session is required")

        if action_type == "confirm_contact":
            confirmed = action.get("confirmed")
            if type(confirmed) is not bool:
                return self._error("confirmed must be a boolean")
            self.session["contact_confirmed"] = confirmed
            prompt = "Choose appointment" if confirmed else "Confirm contact"
            return self._screen(prompt)

        if action_type == "choose_appointment":
            appointment_id = action.get("appointment_id")
            if not isinstance(appointment_id, str):
                return self._error("appointment_id must be a string")
            if appointment_id not in self._appointment_ids:
                return self._error("appointment_id is not known")
            self.session["appointment_id"] = appointment_id
            return self._screen("Review and complete")

        if action_type == "correct":
            field = action.get("field")
            value = action.get("value")
            if field == "contact_confirmed":
                if type(value) is not bool:
                    return self._error("contact_confirmed correction must be boolean")
            elif field == "appointment_id":
                if value is not None and (
                    not isinstance(value, str) or value not in self._appointment_ids
                ):
                    return self._error("appointment_id correction must be known or None")
            else:
                return self._error("field must be contact_confirmed or appointment_id")

            self.session[field] = value
            return self._screen(self._current_prompt())

        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completion")
        if self.session["appointment_id"] not in self._appointment_ids:
            return self._error("an appointment must be selected before completion")
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action_source and screen_sink are required")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
