"""Deterministic console-oriented patient check-in component."""

from copy import deepcopy


_INITIAL_SESSION = {
    "active": False,
    "contact_confirmed": None,
    "appointment_id": None,
    "completed": False,
}

_ACTION_TYPES = {
    "begin",
    "confirm_contact",
    "choose_appointment",
    "correct",
    "complete",
    "abandon",
}


class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self.appointments = deepcopy(list(appointments))
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = dict(_INITIAL_SESSION)
        self._appointment_ids = {item["id"] for item in self.appointments}

    def _reset_session(self):
        self.session.clear()
        self.session.update(_INITIAL_SESSION)

    def _choices(self):
        return [
            {"id": item["id"], "label": item["label"]}
            for item in self.appointments
        ]

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
        return {
            "prompt": prompt,
            "messages": [] if messages is None else list(messages),
            "choices": self._choices() if prompt == "Choose appointment" else [],
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
        if not isinstance(action_type, str) or action_type not in _ACTION_TYPES:
            return self._error("action type is missing or unsupported")

        if action_type == "begin":
            if self.session["active"]:
                return self._error("a check-in session is already active")
            self._reset_session()
            self.session["active"] = True
            return self._screen("Confirm contact")

        if action_type == "abandon":
            if not self.session["active"]:
                return self._error("there is no active session to abandon")
            self._reset_session()
            return self._screen(
                "Session abandoned",
                ["Check-in abandoned"],
            )

        if not self.session["active"]:
            return self._error("an active session is required")

        if self.session["completed"]:
            return self._error("the active session is already complete")

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
                return self._error("appointment_id is not a known appointment")
            self.session["appointment_id"] = appointment_id
            return self._screen("Review and complete")

        if action_type == "correct":
            field = action.get("field")
            if field == "contact_confirmed":
                value = action.get("value")
                if type(value) is not bool:
                    return self._error(
                        "contact_confirmed correction must be a boolean"
                    )
                self.session["contact_confirmed"] = value
                return self._screen(self._next_prompt())

            if field == "appointment_id":
                value = action.get("value")
                if value is not None and not isinstance(value, str):
                    return self._error(
                        "appointment_id correction must be a string or None"
                    )
                if value is not None and value not in self._appointment_ids:
                    return self._error(
                        "appointment_id correction is not a known appointment"
                    )
                self.session["appointment_id"] = value
                return self._screen(self._next_prompt())

            return self._error("field is not correctable")

        if self.session["contact_confirmed"] is not True:
            return self._error("contact must be confirmed before completion")
        if self.session["appointment_id"] is None:
            return self._error("an appointment must be selected before completion")

        self.session["completed"] = True
        self.session["active"] = False
        return self._screen("Check-in complete", ["Checked in"])

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError("run_once requires both an action source and screen sink")

        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
