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
        action_type = action['type']
        if action_type == 'begin':
            if self.session['active']:
                return self._error_screen('Error: Session already active.')
            self.session = {
                'active': True,
                'contact_confirmed': None,
                'appointment_id': None,
                'completed': False
            }
            return self._screen()
        elif action_type == 'confirm_contact':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: No active session.')
            if 'confirmed' not in action or not isinstance(action['confirmed'], bool):
                return self._error_screen('Error: Invalid confirmed value.')
            self.session['contact_confirmed'] = action['confirmed']
            return self._screen()
        elif action_type == 'choose_appointment':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: No active session.')
            if not self.session['contact_confirmed']:
                return self._error_screen('Error: Contact not confirmed.')
            if 'appointment_id' not in action or not isinstance(action['appointment_id'], str):
                return self._error_screen('Error: Invalid appointment id.')
            if not any(app['id'] == action['appointment_id'] for app in self._appointments):
                return self._error_screen('Error: Unknown appointment.')
            self.session['appointment_id'] = action['appointment_id']
            return self._screen()
        elif action_type == 'correct':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: No active session.')
            if 'field' not in action or 'value' not in action:
                return self._error_screen('Error: Missing field or value.')
            field = action['field']
            value = action['value']
            if field == 'contact_confirmed':
                if not isinstance(value, bool):
                    return self._error_screen('Error: Invalid contact value.')
                self.session['contact_confirmed'] = value
            elif field == 'appointment_id':
                if value is not None and (not isinstance(value, str) or not any(app['id'] == value for app in self._appointments)):
                    return self._error_screen('Error: Invalid appointment id.')
                self.session['appointment_id'] = value
            else:
                return self._error_screen('Error: Unknown field.')
            return self._screen()
        elif action_type == 'complete':
            if not self.session['active'] or self.session['completed']:
                return self._error_screen('Error: No active session.')
            if not self.session['contact_confirmed'] or not self.session['appointment_id']:
                return self._error_screen('Error: Incomplete check-in.')
            self.session['completed'] = True
            self.session['active'] = False
            return self._screen()
        elif action_type == 'abandon':
            if not self.session['active']:
                return self._error_screen('Error: No active session.')
            self.session = {
                'active': False,
                'contact_confirmed': None,
                'appointment_id': None,
                'completed': False
            }
            return self._screen()
        else:
            return self._error_screen('Error: Unknown action type.')

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError('Missing source or sink')
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen

    def _screen(self):
        if not self.session['active']:
            if self.session['completed']:
                prompt = 'Check-in complete'
                messages = ['Checked in']
            else:
                prompt = 'Session abandoned'
                messages = ['Check-in abandoned']
            choices = []
        else:
            if self.session['contact_confirmed'] is None:
                prompt = 'Confirm contact'
                messages = []
                choices = []
            elif not self.session['contact_confirmed']:
                prompt = 'Confirm contact'
                messages = []
                choices = []
            elif self.session['appointment_id'] is None:
                prompt = 'Choose appointment'
                messages = []
                choices = [{'id': app['id'], 'label': app['label']} for app in self._appointments]
            else:
                prompt = 'Review and complete'
                messages = []
                choices = []
        return {'prompt': prompt, 'messages': messages, 'choices': choices}

    def _error_screen(self, message):
        return {'prompt': '', 'messages': [message], 'choices': []}
