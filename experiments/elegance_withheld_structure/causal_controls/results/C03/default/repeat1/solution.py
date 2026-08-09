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
        self._validate_items(self._data['items'])

    def _validate_items(self, items):
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('Item must be dict')
            if 'id' not in item or not isinstance(item['id'], str):
                raise ValueError('Item id must be string')
            if item['id'] in seen:
                raise ValueError('Duplicate id')
            seen.add(item['id'])
            if 'kind' not in item or item['kind'] not in ('section', 'group', 'note', 'reference'):
                raise ValueError('Invalid kind')
            if 'title' not in item or not isinstance(item['title'], str):
                raise ValueError('Title must be string')
            if item['kind'] in ('section', 'group'):
                if 'items' not in item or not isinstance(item['items'], list):
                    raise ValueError('Container must have items list')
                self._validate_items(item['items'])
            elif item['kind'] == 'note':
                if 'text' not in item or not isinstance(item['text'], str):
                    raise ValueError('Note must have text')
            elif item['kind'] == 'reference':
                if 'label' not in item or not isinstance(item['label'], str):
                    raise ValueError('Reference must have label')
                if 'word_count' not in item or not isinstance(item['word_count'], int) or item['word_count'] < 0:
                    raise ValueError('Reference word_count must be nonnegative integer')
                if 'unresolved' not in item or not isinstance(item['unresolved'], list) or not all(isinstance(x, str) for x in item['unresolved']):
                    raise ValueError('Reference unresolved must be list of strings')

    def total_word_count(self):
        return self._count_words(self._data['items'])

    def _count_words(self, items):
        total = 0
        for item in items:
            if item['kind'] == 'note':
                text = item['text']
                # Remove marker tokens to avoid counting identifiers as words
                text_without_markers = re.sub(r'\[\?\w+\]', '', text)
                total += len(re.findall(r'\b\w+\b', text_without_markers))
            elif item['kind'] == 'reference':
                total += item['word_count']
            elif item['kind'] in ('section', 'group'):
                total += self._count_words(item['items'])
        return total

    def unresolved_markers(self):
        markers = set()
        self._collect_markers(self._data['items'], markers)
        return sorted(markers)

    def _collect_markers(self, items, markers):
        for item in items:
            if item['kind'] == 'note':
                for m in re.findall(r'\[\?([^\]]+)\]', item['text']):
                    markers.add(m)
            elif item['kind'] == 'reference':
                for m in item['unresolved']:
                    markers.add(m)
            elif item['kind'] in ('section', 'group'):
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
            raise ValueError('IDs must be strings')
        # Deep copy original data for rollback
        original = copy.deepcopy(self._data)
        try:
            # Find item and its parent
            item, parent_items, parent_index = self._find_item(self._data['items'], item_id)
            if item is None:
                raise ValueError('Item not found')
            # Determine destination container
            if destination_id == 'workspace':
                dest_items = self._data['items']
            else:
                dest_item, dest_parent, dest_index = self._find_item(self._data['items'], destination_id)
                if dest_item is None:
                    raise ValueError('Destination not found')
                if dest_item['kind'] not in ('section', 'group'):
                    raise ValueError('Destination not a container')
                dest_items = dest_item['items']
            # Check moving container into itself or descendant
            if self._is_descendant(item, destination_id):
                raise ValueError('Cannot move container into itself')
            # Remove item from original location
            parent_items.pop(parent_index)
            # Compute insertion index
            if index is None:
                insert_index = len(dest_items)
            else:
                if not isinstance(index, int) or index < 0 or index > len(dest_items):
                    raise ValueError('Invalid index')
                insert_index = index
            # Insert item
            dest_items.insert(insert_index, item)
        except Exception:
            self._data = original
            raise

    def _find_item(self, items, item_id):
        for i, item in enumerate(items):
            if item['id'] == item_id:
                return item, items, i
            if item['kind'] in ('section', 'group'):
                found = self._find_item(item['items'], item_id)
                if found[0] is not None:
                    return found
        return None, None, None

    def _is_descendant(self, item, dest_id):
        # Check if dest_id is inside item (including item itself)
        if item['id'] == dest_id:
            return True
        if item['kind'] in ('section', 'group'):
            for child in item['items']:
                if self._is_descendant(child, dest_id):
                    return True
        return False

    def to_data(self):
        return copy.deepcopy(self._data)
