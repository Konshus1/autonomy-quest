import copy

class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self.appointments = copy.deepcopy(list(appointments))
        self.action_source = action_source
        self.screen_sink = screen_sink
        self.session = {
            'active': False,
            'contact_confirmed': None,
            'appointment_id': None,
            'completed': False
        }

    def _make_screen(self, prompt, messages=None, choices=None):
        if messages is None:
            messages = []
        if choices is None:
            choices = []
        return {
            'prompt': prompt,
            'messages': messages,
            'choices': choices
        }

    def _error_screen(self, message):
        return self._make_screen(
            prompt=self._current_prompt(),
            messages=[f'Error: {message}']
        )

    def _current_prompt(self):
        if not self.session['active']:
            return 'Check-in complete' if self.session['completed'] else 'Session abandoned'
        if not self.session['contact_confirmed']:
            return 'Confirm contact'
        if self.session['appointment_id'] is None:
            return 'Choose appointment'
        return 'Review and complete'

    def _appointment_choices(self):
        if self.session['active'] and self.session['contact_confirmed'] and self.session['appointment_id'] is None:
            return [{'id': a['id'], 'label': a['label']} for a in self.appointments]
        return []

    def handle(self, action):
        if not isinstance(action, dict) or 'type' not in action:
            return self._error_screen('Malformed action')

        atype = action['type']

        if atype == 'begin':
            if self.session['active']:
                return self._error_screen('Session already active')
            self.session = {
                'active': True,
                'contact_confirmed': None,
                'appointment_id': None,
                'completed': False
            }
            return self._make_screen(prompt='Confirm contact')

        if atype == 'confirm_contact':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('No active session')
            if 'confirmed' not in action or not isinstance(action['confirmed'], bool):
                return self._error_screen('Invalid confirmed value')
            self.session['contact_confirmed'] = action['confirmed']
            if self.session['contact_confirmed']:
                return self._make_screen(prompt='Choose appointment')
            else:
                return self._make_screen(prompt='Confirm contact')

        if atype == 'choose_appointment':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('No active session')
            if not self.session['contact_confirmed']:
                return self._error_screen('Contact not confirmed')
            if 'appointment_id' not in action or not isinstance(action['appointment_id'], str):
                return self._error_screen('Invalid appointment id')
            if not any(a['id'] == action['appointment_id'] for a in self.appointments):
                return self._error_screen('Unknown appointment')
            self.session['appointment_id'] = action['appointment_id']
            return self._make_screen(prompt='Review and complete')

        if atype == 'correct':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('No active session')
            if 'field' not in action or 'value' not in action:
                return self._error_screen('Missing correction fields')
            field = action['field']
            value = action['value']
            if field == 'contact_confirmed':
                if not isinstance(value, bool):
                    return self._error_screen('Invalid contact value')
                self.session['contact_confirmed'] = value
            elif field == 'appointment_id':
                if value is not None and (not isinstance(value, str) or not any(a['id'] == value for a in self.appointments)):
                    return self._error_screen('Invalid appointment id')
                self.session['appointment_id'] = value
            else:
                return self._error_screen('Unknown field')
            if self.session['contact_confirmed'] and self.session['appointment_id'] is None:
                prompt = 'Choose appointment'
            elif not self.session['contact_confirmed']:
                prompt = 'Confirm contact'
            else:
                prompt = 'Review and complete'
            return self._make_screen(prompt=prompt)

        if atype == 'complete':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('No active session')
            if not self.session['contact_confirmed'] or self.session['appointment_id'] is None:
                return self._error_screen('Incomplete check-in')
            self.session['active'] = False
            self.session['completed'] = True
            return self._make_screen(prompt='Check-in complete', messages=['Checked in'])

        if atype == 'abandon':
            if not self.session['active']:
                return self._error_screen('No active session')
            self.session = {
                'active': False,
                'contact_confirmed': None,
                'appointment_id': None,
                'completed': False
            }
            return self._make_screen(prompt='Session abandoned', messages=['Check-in abandoned'])

        return self._error_screen('Unknown action type')

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError('Missing source or sink')
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen
