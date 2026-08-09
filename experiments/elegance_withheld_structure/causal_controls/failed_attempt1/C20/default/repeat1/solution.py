import copy

class CheckInConsole:
    def __init__(self, appointments, action_source=None, screen_sink=None):
        self._appointments = copy.deepcopy(list(appointments))
        self.session = {
            'active': False,
            'contact_confirmed': None,
            'appointment_id': None,
            'completed': False
        }
        self._action_source = action_source
        self._screen_sink = screen_sink

    def _make_screen(self, prompt, messages=None, choices=None):
        return {
            'prompt': prompt,
            'messages': messages if messages is not None else [],
            'choices': choices if choices is not None else []
        }

    def _appointment_choices(self):
        return [{'id': app['id'], 'label': app['label']} for app in self._appointments]

    def _next_prompt(self):
        if not self.session['active']:
            return 'Confirm contact'
        if not self.session['contact_confirmed']:
            return 'Confirm contact'
        if self.session['appointment_id'] is None:
            return 'Choose appointment'
        return 'Review and complete'

    def handle(self, action):
        if not isinstance(action, dict) or 'type' not in action:
            return self._make_screen('Error', ['Error: Malformed action'])

        atype = action['type']

        if atype == 'begin':
            if self.session['active']:
                return self._make_screen('Error', ['Error: Session already active'])
            self.session = {
                'active': True,
                'contact_confirmed': None,
                'appointment_id': None,
                'completed': False
            }
            return self._make_screen('Confirm contact')

        if atype == 'confirm_contact':
            if not self.session['active'] or self.session['completed']:
                return self._make_screen('Error', ['Error: No active session'])
            if 'confirmed' not in action or not isinstance(action['confirmed'], bool):
                return self._make_screen('Error', ['Error: Invalid confirmed value'])
            self.session['contact_confirmed'] = action['confirmed']
            prompt = self._next_prompt()
            return self._make_screen(prompt)

        if atype == 'choose_appointment':
            if not self.session['active'] or self.session['completed']:
                return self._make_screen('Error', ['Error: No active session'])
            if not self.session['contact_confirmed']:
                return self._make_screen('Error', ['Error: Contact not confirmed'])
            if 'appointment_id' not in action or not isinstance(action['appointment_id'], str):
                return self._make_screen('Error', ['Error: Invalid appointment id'])
            app_id = action['appointment_id']
            if not any(app['id'] == app_id for app in self._appointments):
                return self._make_screen('Error', ['Error: Unknown appointment'])
            self.session['appointment_id'] = app_id
            return self._make_screen('Review and complete')

        if atype == 'correct':
            if not self.session['active'] or self.session['completed']:
                return self._make_screen('Error', ['Error: No active session'])
            if 'field' not in action or 'value' not in action:
                return self._make_screen('Error', ['Error: Missing correction fields'])
            field = action['field']
            value = action['value']
            if field == 'contact_confirmed':
                if not isinstance(value, bool):
                    return self._make_screen('Error', ['Error: Invalid contact value'])
                self.session['contact_confirmed'] = value
            elif field == 'appointment_id':
                if value is not None and (not isinstance(value, str) or not any(app['id'] == value for app in self._appointments)):
                    return self._make_screen('Error', ['Error: Invalid appointment id'])
                self.session['appointment_id'] = value
            else:
                return self._make_screen('Error', ['Error: Unknown field'])
            prompt = self._next_prompt()
            return self._make_screen(prompt)

        if atype == 'complete':
            if not self.session['active'] or self.session['completed']:
                return self._make_screen('Error', ['Error: No active session'])
            if not self.session['contact_confirmed'] or self.session['appointment_id'] is None:
                return self._make_screen('Error', ['Error: Incomplete check-in'])
            self.session['active'] = False
            self.session['completed'] = True
            return self._make_screen('Check-in complete', ['Checked in'])

        if atype == 'abandon':
            if not self.session['active']:
                return self._make_screen('Error', ['Error: No active session'])
            self.session = {
                'active': False,
                'contact_confirmed': None,
                'appointment_id': None,
                'completed': False
            }
            return self._make_screen('Session abandoned', ['Check-in abandoned'])

        return self._make_screen('Error', ['Error: Unknown action type'])

    def run_once(self):
        if self._action_source is None or self._screen_sink is None:
            raise RuntimeError('Missing source or sink')
        action = self._action_source.read_action()
        screen = self.handle(action)
        self._screen_sink.display(screen)
        return screen
