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

    def handle(self, action):
        if not isinstance(action, dict) or 'type' not in action:
            return self._error_screen('Error: Malformed action.')
        atype = action['type']
        if atype == 'begin':
            if self.session['active']:
                return self._error_screen('Error: Session already active.')
            self.session = {
                'active': True,
                'contact_confirmed': None,
                'appointment_id': None,
                'completed': False
            }
            return self._screen('Confirm contact', [], [])
        elif atype == 'confirm_contact':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: No active session.')
            if 'confirmed' not in action or not isinstance(action['confirmed'], bool):
                return self._error_screen('Error: Invalid confirmed value.')
            self.session['contact_confirmed'] = action['confirmed']
            return self._screen(self._next_prompt(), [], [])
        elif atype == 'choose_appointment':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: No active session.')
            if not self.session['contact_confirmed']:
                return self._error_screen('Error: Contact not confirmed.')
            if 'appointment_id' not in action or not isinstance(action['appointment_id'], str):
                return self._error_screen('Error: Invalid appointment id.')
            if not any(a['id'] == action['appointment_id'] for a in self.appointments):
                return self._error_screen('Error: Unknown appointment id.')
            self.session['appointment_id'] = action['appointment_id']
            return self._screen('Review and complete', [], [])
        elif atype == 'correct':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: No active session.')
            if 'field' not in action or 'value' not in action:
                return self._error_screen('Error: Missing correction fields.')
            field = action['field']
            value = action['value']
            if field == 'contact_confirmed':
                if not isinstance(value, bool):
                    return self._error_screen('Error: Invalid contact value.')
                self.session['contact_confirmed'] = value
            elif field == 'appointment_id':
                if value is not None and (not isinstance(value, str) or not any(a['id'] == value for a in self.appointments)):
                    return self._error_screen('Error: Invalid appointment id.')
                self.session['appointment_id'] = value
            else:
                return self._error_screen('Error: Unknown field.')
            return self._screen(self._next_prompt(), [], [])
        elif atype == 'complete':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: No active session.')
            if not self.session['contact_confirmed'] or self.session['appointment_id'] is None:
                return self._error_screen('Error: Missing required information.')
            self.session['completed'] = True
            self.session['active'] = False
            return self._screen('Check-in complete', ['Checked in'], [])
        elif atype == 'abandon':
            if not self.session['active']:
                return self._error_screen('Error: No active session.')
            self.session = {
                'active': False,
                'contact_confirmed': None,
                'appointment_id': None,
                'completed': False
            }
            return self._screen('Session abandoned', ['Check-in abandoned'], [])
        else:
            return self._error_screen('Error: Unknown action type.')

    def run_once(self):
        if self.action_source is None or self.screen_sink is None:
            raise RuntimeError('Missing source or sink')
        action = self.action_source.read_action()
        screen = self.handle(action)
        self.screen_sink.display(screen)
        return screen

    def _next_prompt(self):
        if not self.session['active']:
            return 'Check-in complete' if self.session['completed'] else 'Session abandoned'
        if self.session['contact_confirmed'] is None:
            return 'Confirm contact'
        if not self.session['contact_confirmed']:
            return 'Confirm contact'
        if self.session['appointment_id'] is None:
            return 'Choose appointment'
        return 'Review and complete'

    def _screen(self, prompt, messages, choices):
        return {
            'prompt': prompt,
            'messages': messages,
            'choices': choices
        }

    def _error_screen(self, message):
        return self._screen('', [message], [])
