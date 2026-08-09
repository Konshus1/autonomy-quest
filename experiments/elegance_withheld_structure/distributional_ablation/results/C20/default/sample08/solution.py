class CheckInConsole:
    INITIAL_SESSION = {
        "active": False,
        "contact_confirmed": None,
        "appointment_id": None,
        "completed": False,
    }

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self.appointments = [
            {"id": appointment["id"], "label": appointment["label"]}
            for appointment in appointments
        ]
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = self.INITIAL_SESSION.copy()
        self._appointment_ids = {appointment["id"] for appointment in self.appointments}

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
            choices = [appointment.copy() for appointment in self.appointments]
        return {
            "prompt": prompt,
            "messages": list(messages or []),
            "choices": choices,
        }

    def _error(self, message):
        return self._screen(self._next_prompt(), ["Error: " + message])

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error("action must be a dictionary")

        action_type = action.get("type")
        if action_type == "begin":
            return self._begin()
        if action_type == "confirm_contact":
            return self._confirm_contact(action)
        if action_type == "choose_appointment":
            return self._choose_appointment(action)
        if action_type == "correct":
            return self._correct(action)
        if action_type == "complete":
            return self._complete()
        if action_type == "abandon":
            return self._abandon()
        return self._error("unknown or missing action type")

    def _begin(self):
        if self.session["active"]:
            return self._error("a session is already active")
        self.session.clear()
        self.session.update(self.INITIAL_SESSION)
        self.session["active"] = True
        return self._screen("Confirm contact")

    def _require_active_incomplete(self):
        if not self.session["active"]:
            return "no session is active"
        if self.session["completed"]:
            return "the session is already complete"
        return None

    def _confirm_contact(self, action):
        error = self._require_active_incomplete()
        if error:
            return self._error(error)
        if "confirmed" not in action or type(action["confirmed"]) is not bool:
            return self._error("confirmed must be a boolean")

        self.session["contact_confirmed"] = action["confirmed"]
        prompt = "Choose appointment" if action["confirmed"] else "Confirm contact"
        return self._screen(prompt)

    def _choose_appointment(self, action):
        error = self._require_active_incomplete()
        if error:
            return self._error(error)
        appointment_id = action.get("appointment_id")
        if type(appointment_id) is not str:
            return self._error("appointment_id must be a string")
        if appointment_id not in self._appointment_ids:
            return self._error("appointment_id is not known")

        self.session["appointment_id"] = appointment_id
        return self._screen("Review and complete")

    def _correct(self, action):
        error = self._require_active_incomplete()
        if error:
            return self._error(error)
        if "field" not in action or "value" not in action:
            return self._error("correction requires field and value")

        field = action["field"]
        value = action["value"]
        if field == "contact_confirmed":
            if type(value) is not bool:
                return self._error("contact_confirmed correction must be boolean")
        elif field == "appointment_id":
            if value is not None and (
                type(value) is not str or value not in self._appointment_ids
            ):
                return self._error("appointment_id correction must be a known id or None")
        else:
            return self._error("field is not correctable")

        self.session[field] = value
        return self._screen(self._next_prompt())

    def _complete(self):
        error = self._require_active_incomplete()
        if error:
            return self._error(error)
        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completion")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be selected before completion")

        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def _abandon(self):
        if not self.session["active"]:
            return self._error("no session is active")

        self.session.clear()
        self.session.update(self.INITIAL_SESSION)
        return self._screen("Session abandoned", ["Check-in abandoned"])

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("action source and screen sink are required")
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
