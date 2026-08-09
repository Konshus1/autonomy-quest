import copy
import re

class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self._validate_data()

    def _validate_data(self):
        # Basic validation: title must be str, items list
        if not isinstance(self._data.get('title'), str):
            raise ValueError('title must be a string')
        if not isinstance(self._data.get('items'), list):
            raise ValueError('items must be a list')
        self._validate_items(self._data['items'])

    def _validate_items(self, items):
        for item in items:
            if not isinstance(item.get('id'), str):
                raise ValueError('id must be a string')
            kind = item.get('kind')
            if kind not in ('section', 'group', 'note', 'reference'):
                raise ValueError('invalid kind')
            if kind in ('section', 'group'):
                if not isinstance(item.get('items'), list):
                    raise ValueError('container items must be a list')
                self._validate_items(item['items'])
            elif kind == 'note':
                if not isinstance(item.get('text'), str):
                    raise ValueError('note text must be a string')
            elif kind == 'reference':
                if not isinstance(item.get('label'), str):
                    raise ValueError('reference label must be a string')
                if not isinstance(item.get('word_count'), int) or item['word_count'] < 0:
                    raise ValueError('word_count must be nonnegative integer')
                if not isinstance(item.get('unresolved'), list) or not all(isinstance(m, str) for m in item['unresolved']):
                    raise ValueError('unresolved must be list of strings')

    def total_word_count(self):
        return self._count_words(self._data['items'])

    def _count_words(self, items):
        total = 0
        for item in items:
            kind = item['kind']
            if kind == 'note':
                total += self._note_word_count(item['text'])
            elif kind == 'reference':
                total += item['word_count']
            elif kind in ('section', 'group'):
                total += self._count_words(item['items'])
        return total

    @staticmethod
    def _note_word_count(text):
        # Remove marker tokens and count words
        text_without_markers = re.sub(r'\[\?[^\]]+\]', '', text)
        return len(re.findall(r'\b\w+\b', text_without_markers))

    def unresolved_markers(self):
        markers = set()
        self._collect_markers(self._data['items'], markers)
        return sorted(markers)

    def _collect_markers(self, items, markers):
        for item in items:
            kind = item['kind']
            if kind == 'note':
                for m in re.findall(r'\[\?([^\]]+)\]', item['text']):
                    markers.add(m)
            elif kind == 'reference':
                for m in item['unresolved']:
                    markers.add(m)
            elif kind in ('section', 'group'):
                self._collect_markers(item['items'], markers)

    def outline(self):
        lines = [self._data['title']]
        self._build_outline(self._data['items'], 0, lines)
        return '\n'.join(lines)

    def _build_outline(self, items, depth, lines):
        for item in items:
            lines.append('  ' * depth + f"{item['kind']}: {item['title']}")
            if item['kind'] in ('section', 'group'):
                self._build_outline(item['items'], depth + 1, lines)

    def move(self, item_id, destination_id, index=None):
        # Validate ids are strings
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError('ids must be strings')

        # Find item and its parent
        item_info = self._find_item(self._data['items'], item_id)
        if item_info is None:
            raise ValueError('item not found')
        item, parent, parent_key, idx = item_info

        # Determine destination container
        if destination_id == 'workspace':
            dest_container = self._data['items']
            dest_parent = None
        else:
            dest_info = self._find_item(self._data['items'], destination_id)
            if dest_info is None:
                raise ValueError('destination not found')
            dest_item, dest_parent, dest_parent_key, dest_idx = dest_info
            if dest_item['kind'] not in ('section', 'group'):
                raise ValueError('destination is not a container')
            dest_container = dest_item['items']

        # Check for moving container into itself or descendant
        if item['kind'] in ('section', 'group'):
            if self._is_descendant(item, destination_id):
                raise ValueError('cannot move container into itself or descendant')

        # Validate index
        if index is None:
            index = len(dest_container)
        else:
            if not isinstance(index, int):
                raise ValueError('index must be integer')
            if index < 0 or index > len(dest_container):
                raise ValueError('index out of range')

        # Remove item from current location
        if parent is None:
            self._data['items'].pop(idx)
        else:
            parent[parent_key].pop(idx)

        # Insert into destination
        dest_container.insert(index, item)

    def _find_item(self, items, target_id, parent=None, parent_key=None):
        for i, item in enumerate(items):
            if item['id'] == target_id:
                return (item, parent, parent_key, i)
            if item['kind'] in ('section', 'group'):
                result = self._find_item(item['items'], target_id, item, 'items')
                if result:
                    return result
        return None

    def _is_descendant(self, container_item, target_id):
        # Check if target_id is inside container_item's subtree
        for child in container_item.get('items', []):
            if child['id'] == target_id:
                return True
            if child['kind'] in ('section', 'group'):
                if self._is_descendant(child, target_id):
                    return True
        return False

    def to_data(self):
        return copy.deepcopy(self._data)
