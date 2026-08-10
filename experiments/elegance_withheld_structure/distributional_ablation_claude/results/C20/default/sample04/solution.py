"""Console patient check-in: structured actions in, deterministic screens out."""

PROMPT_BEGIN = "Begin check-in"
PROMPT_CONFIRM = "Confirm contact"
PROMPT_CHOOSE = "Choose appointment"
PROMPT_REVIEW = "Review and complete"
PROMPT_COMPLETE = "Check-in complete"
PROMPT_ABANDONED = "Session abandoned"

MSG_COMPLETED = "Checked in"
MSG_ABANDONED = "Check-in abandoned"


class CheckInConsole:
    """Drives a patient check-in session from structured actions.

    ``action_source`` must provide ``read_action()`` and ``screen_sink``
    must provide ``display(screen)``; both are optional until ``run_once``.
    """

    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = [
            {"id": item["id"], "label": item["label"]} for item in appointments
        ]
        self._action_source = action_source
        self._screen_sink = screen_sink
        self.session = self._fresh_session()

    @staticmethod
    def _fresh_session():
        return {
            "active": False,
            "contact_confirmed": None,
            "appointment_id": None,
            "completed": False,
        }

    def handle(self, action):
        if not isinstance(action, dict):
            return self._error_screen("action must be a dictionary")
        action_type = action.get("type")
        if not isinstance(action_type, str):
            return self._error_screen("action type must be a string")
        handlers = {
            "begin": self._handle_begin,
            "confirm_contact": self._handle_confirm_contact,
            "choose_appointment": self._handle_choose_appointment,
            "correct": self._handle_correct,
            "complete": self._handle_complete,
            "abandon": self._handle_abandon,
        }
        handler = handlers.get(action_type)
        if handler is None:
            return self._error_screen("unknown action type '%s'" % action_type)
        return handler(action)

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError("run_once requires an action_source and a screen_sink")
        screen = self.handle(self._action_source.read_action())
        self._screen_sink.display(screen)
        return screen

    # Action handlers: each validates fully before touching the session.

    def _handle_begin(self, action):
        if self.session["active"]:
            return self._error_screen("a session is already active")
        self.session = self._fresh_session()
        self.session["active"] = True
        return self._screen(PROMPT_CONFIRM)

    def _handle_confirm_contact(self, action):
        error = self._require_open_session()
        if error is not None:
            return error
        confirmed = action.get("confirmed")
        if not isinstance(confirmed, bool):
            return self._error_screen("'confirmed' must be a boolean")
        self.session["contact_confirmed"] = confirmed
        return self._screen(PROMPT_CHOOSE if confirmed else PROMPT_CONFIRM)

    def _handle_choose_appointment(self, action):
        error = self._require_open_session()
        if error is not None:
            return error
        appointment_id = action.get("appointment_id")
        if appointment_id not in self._known_ids():
            return self._error_screen(
                "unknown appointment id %r" % (appointment_id,)
            )
        self.session["appointment_id"] = appointment_id
        return self._screen(PROMPT_REVIEW)

    def _handle_correct(self, action):
        error = self._require_open_session()
        if error is not None:
            return error
        field = action.get("field")
        value = action.get("value")
        if field == "contact_confirmed":
            if not isinstance(value, bool):
                return self._error_screen("'contact_confirmed' must be a boolean")
        elif field == "appointment_id":
            if value is not None and value not in self._known_ids():
                return self._error_screen("unknown appointment id %r" % (value,))
        else:
            return self._error_screen("cannot correct field %r" % (field,))
        self.session[field] = value
        return self._screen(self._next_prompt())

    def _handle_complete(self, action):
        error = self._require_open_session()
        if error is not None:
            return error
        if self.session["contact_confirmed"] is not True:
            return self._error_screen("contact must be confirmed before completion")
        if self.session["appointment_id"] is None:
            return self._error_screen(
                "an appointment must be chosen before completion"
            )
        self.session["completed"] = True
        self.session["active"] = False
        return self._screen(PROMPT_COMPLETE, [MSG_COMPLETED])

    def _handle_abandon(self, action):
        if not self.session["active"]:
            return self._error_screen("no active session to abandon")
        self.session = self._fresh_session()
        return self._screen(PROMPT_ABANDONED, [MSG_ABANDONED])

    # Helpers.

    def _require_open_session(self):
        if not self.session["active"] or self.session["completed"]:
            return self._error_screen("no active session in progress")
        return None

    def _known_ids(self):
        return [item["id"] for item in self._appointments]

    def _next_prompt(self):
        if self.session["completed"]:
            return PROMPT_COMPLETE
        if not self.session["active"]:
            return PROMPT_BEGIN
        if self.session["contact_confirmed"] is not True:
            return PROMPT_CONFIRM
        if self.session["appointment_id"] is None:
            return PROMPT_CHOOSE
        return PROMPT_REVIEW

    def _screen(self, prompt, messages=None):
        if prompt == PROMPT_CHOOSE:
            choices = [dict(item) for item in self._appointments]
        else:
            choices = []
        return {
            "prompt": prompt,
            "messages": list(messages) if messages else [],
            "choices": choices,
        }

    def _error_screen(self, reason):
        return self._screen(self._next_prompt(), ["Error: " + reason])
