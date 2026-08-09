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
            return self._error_screen('Error: malformed action')
        atype = action['type']
        if atype == 'begin':
            if self.session['active']:
                return self._error_screen('Error: session already active')
            self.session = {
                'active': True,
                'contact_confirmed': None,
                'appointment_id': None,
                'completed': False
            }
            return self._screen_for_state()
        elif atype == 'confirm_contact':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: no active session')
            if 'confirmed' not in action or not isinstance(action['confirmed'], bool):
                return self._error_screen('Error: invalid confirmed value')
            self.session['contact_confirmed'] = action['confirmed']
            return self._screen_for_state()
        elif atype == 'choose_appointment':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: no active session')
            if not self.session['contact_confirmed']:
                return self._error_screen('Error: contact not confirmed')
            if 'appointment_id' not in action or not isinstance(action['appointment_id'], str):
                return self._error_screen('Error: invalid appointment id')
            if not any(a['id'] == action['appointment_id'] for a in self._appointments):
                return self._error_screen('Error: unknown appointment')
            self.session['appointment_id'] = action['appointment_id']
            return self._screen_for_state()
        elif atype == 'correct':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: no active session')
            if 'field' not in action or 'value' not in action:
                return self._error_screen('Error: missing correction fields')
            field = action['field']
            value = action['value']
            if field == 'contact_confirmed':
                if not isinstance(value, bool):
                    return self._error_screen('Error: invalid contact value')
                self.session['contact_confirmed'] = value
            elif field == 'appointment_id':
                if value is not None and (not isinstance(value, str) or not any(a['id'] == value for a in self._appointments)):
                    return self._error_screen('Error: invalid appointment id')
                self.session['appointment_id'] = value
            else:
                return self._error_screen('Error: unknown field')
            return self._screen_for_state()
        elif atype == 'complete':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: no active session')
            if not self.session['contact_confirmed'] or not self.session['appointment_id']:
                return self._error_screen('Error: incomplete check-in')
            self.session['completed'] = True
            self.session['active'] = False
            return self._screen_for_state()
        elif atype == 'abandon':
            if not self.session['active']:
                return self._error_screen('Error: no active session')
            self.session = {
                'active': False,
                'contact_confirmed': None,
                'appointment_id': None,
                'completed': False
            }
            return self._screen_for_state()
        else:
            return self._error_screen('Error: unknown action type')

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError('source and sink required')
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen

    def _screen_for_state(self):
        if not self.session['active']:
            if self.session['completed']:
                return {
                    'prompt': 'Check-in complete',
                    'messages': ['Checked in'],
                    'choices': []
                }
            else:
                return {
                    'prompt': 'Session abandoned',
                    'messages': ['Check-in abandoned'],
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
            'prompt': '',
            'messages': [message],
            'choices': []
        }
