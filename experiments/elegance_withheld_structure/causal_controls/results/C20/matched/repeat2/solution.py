import copy

class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = copy.deepcopy(list(appointments))
        self._action_source = action_source
        self._screen_sink = screen_sink
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
            return self._screen_for_state()
        elif atype == 'confirm_contact':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: No active session.')
            if 'confirmed' not in action or not isinstance(action['confirmed'], bool):
                return self._error_screen('Error: Invalid confirmed value.')
            self.session['contact_confirmed'] = action['confirmed']
            return self._screen_for_state()
        elif atype == 'choose_appointment':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: No active session.')
            if not self.session['contact_confirmed']:
                return self._error_screen('Error: Contact not confirmed.')
            if 'appointment_id' not in action or not isinstance(action['appointment_id'], str):
                return self._error_screen('Error: Invalid appointment id.')
            if not any(a['id'] == action['appointment_id'] for a in self._appointments):
                return self._error_screen('Error: Unknown appointment.')
            self.session['appointment_id'] = action['appointment_id']
            return self._screen_for_state()
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
                if value is False:
                    self.session['appointment_id'] = None
                return self._screen_for_state()
            elif field == 'appointment_id':
                if value is not None and (not isinstance(value, str) or not any(a['id'] == value for a in self._appointments)):
                    return self._error_screen('Error: Invalid appointment id.')
                self.session['appointment_id'] = value
                return self._screen_for_state()
            else:
                return self._error_screen('Error: Unknown field.')
        elif atype == 'complete':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: No active session.')
            if not self.session['contact_confirmed'] or self.session['appointment_id'] is None:
                return self._error_screen('Error: Incomplete check-in.')
            self.session['active'] = False
            self.session['completed'] = True
            return {
                'prompt': 'Check-in complete',
                'messages': ['Checked in'],
                'choices': []
            }
        elif atype == 'abandon':
            if not self.session['active']:
                return self._error_screen('Error: No active session.')
            self.session = {
                'active': False,
                'contact_confirmed': None,
                'appointment_id': None,
                'completed': False
            }
            return {
                'prompt': 'Session abandoned',
                'messages': ['Check-in abandoned'],
                'choices': []
            }
        else:
            return self._error_screen('Error: Unknown action type.')

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError('Missing source or sink')
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen

    def _screen_for_state(self):
        if not self.session['active']:
            return {
                'prompt': 'Check-in complete' if self.session['completed'] else 'Session abandoned',
                'messages': ['Checked in'] if self.session['completed'] else ['Check-in abandoned'],
                'choices': []
            }
        if self.session['contact_confirmed'] is None:
            return {
                'prompt': 'Confirm contact',
                'messages': [],
                'choices': []
            }
        if not self.session['contact_confirmed']:
            return {
                'prompt': 'Confirm contact',
                'messages': [],
                'choices': []
            }
        if self.session['appointment_id'] is None:
            return {
                'prompt': 'Choose appointment',
                'messages': [],
                'choices': [{'id': a['id'], 'label': a['label']} for a in self._appointments]
            }
        return {
            'prompt': 'Review and complete',
            'messages': [],
            'choices': []
        }

    def _error_screen(self, message):
        return {
            'prompt': self._current_prompt(),
            'messages': [message],
            'choices': self._current_choices()
        }

    def _current_prompt(self):
        if not self.session['active']:
            return 'Check-in complete' if self.session['completed'] else 'Session abandoned'
        if self.session['contact_confirmed'] is None or not self.session['contact_confirmed']:
            return 'Confirm contact'
        if self.session['appointment_id'] is None:
            return 'Choose appointment'
        return 'Review and complete'

    def _current_choices(self):
        if self.session['active'] and self.session['contact_confirmed'] and self.session['appointment_id'] is None:
            return [{'id': a['id'], 'label': a['label']} for a in self._appointments]
        return []
