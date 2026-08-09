import re
import copy

class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self._validate()

    def _validate(self):
        if not isinstance(self._data, dict) or 'title' not in self._data or 'items' not in self._data:
            raise ValueError('Invalid workspace data')
        if not isinstance(self._data['title'], str):
            raise ValueError('Title must be string')
        if not isinstance(self._data['items'], list):
            raise ValueError('Items must be list')
        seen = set()
        self._validate_items(self._data['items'], seen)

    def _validate_items(self, items, seen):
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('Item must be dict')
            if 'id' not in item or not isinstance(item['id'], str):
                raise ValueError('Item id must be string')
            if item['id'] in seen:
                raise ValueError('Duplicate id')
            seen.add(item['id'])
            kind = item.get('kind')
            if kind not in ('section', 'group', 'note', 'reference'):
                raise ValueError('Invalid kind')
            if kind in ('section', 'group'):
                if 'items' not in item or not isinstance(item['items'], list):
                    raise ValueError('Container must have items list')
                self._validate_items(item['items'], seen)
            elif kind == 'note':
                if 'text' not in item or not isinstance(item['text'], str):
                    raise ValueError('Note must have text')
            elif kind == 'reference':
                if 'label' not in item or not isinstance(item['label'], str):
                    raise ValueError('Reference must have label')
                if 'word_count' not in item or not isinstance(item['word_count'], int) or item['word_count'] < 0:
                    raise ValueError('Reference word_count must be nonnegative int')
                if 'unresolved' not in item or not isinstance(item['unresolved'], list) or not all(isinstance(x, str) for x in item['unresolved']):
                    raise ValueError('Reference unresolved must be list of strings')

    def total_word_count(self):
        return self._count_items(self._data['items'])

    def _count_items(self, items):
        total = 0
        for item in items:
            kind = item['kind']
            if kind in ('section', 'group'):
                total += self._count_items(item['items'])
            elif kind == 'note':
                total += self._count_words(item['text'])
            elif kind == 'reference':
                total += item['word_count']
        return total

    @staticmethod
    def _count_words(text):
        # Remove marker tokens? Actually markers are not words, but regex \b\w+\b will not match brackets.
        # So just count all word sequences.
        return len(re.findall(r'\b\w+\b', text))

    def unresolved_markers(self):
        markers = set()
        self._collect_markers(self._data['items'], markers)
        return sorted(markers)

    def _collect_markers(self, items, markers):
        for item in items:
            kind = item['kind']
            if kind in ('section', 'group'):
                self._collect_markers(item['items'], markers)
            elif kind == 'note':
                for m in re.findall(r'\[\[\?([^\]]+)\]\]', item['text']):
                    markers.add(m)
            elif kind == 'reference':
                for m in item['unresolved']:
                    markers.add(m)

    def outline(self):
        lines = [self._data['title']]
        self._outline_items(self._data['items'], 0, lines)
        return '\n'.join(lines)

    def _outline_items(self, items, depth, lines):
        for item in items:
            prefix = '  ' * depth
            lines.append(f"{prefix}{item['kind']}: {item['title']}")
            if item['kind'] in ('section', 'group'):
                self._outline_items(item['items'], depth + 1, lines)

    def move(self, item_id, destination_id, index=None):
        # Validate ids are strings
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError('IDs must be strings')
        # Find item and its parent
        item_info = self._find_item(self._data['items'], item_id)
        if item_info is None:
            raise ValueError('Item not found')
        item, parent_items = item_info
        # Determine destination container
        if destination_id == 'workspace':
            dest_items = self._data['items']
        else:
            dest_info = self._find_item(self._data['items'], destination_id)
            if dest_info is None:
                raise ValueError('Destination not found')
            dest_item, _ = dest_info
            if dest_item['kind'] not in ('section', 'group'):
                raise ValueError('Destination not a container')
            dest_items = dest_item['items']
        # Validate index
        if index is None:
            index = len(dest_items)
        else:
            if not isinstance(index, int) or index < 0 or index > len(dest_items):
                raise ValueError('Invalid index')
        # Prevent moving container into itself or descendant
        if self._is_descendant(item, destination_id):
            raise ValueError('Cannot move container into itself')
        # Remove item from original location
        parent_items.remove(item)
        # Insert at destination
        dest_items.insert(index, item)

    def _find_item(self, items, target_id):
        for i, item in enumerate(items):
            if item['id'] == target_id:
                return item, items
            if item['kind'] in ('section', 'group'):
                found = self._find_item(item['items'], target_id)
                if found is not None:
                    return found
        return None

    def _is_descendant(self, item, target_id):
        # Check if target_id is within item's subtree (including item itself)
        if item['id'] == target_id:
            return True
        if item['kind'] in ('section', 'group'):
            for child in item['items']:
                if self._is_descendant(child, target_id):
                    return True
        return False

    def to_data(self):
        return copy.deepcopy(self._data)
