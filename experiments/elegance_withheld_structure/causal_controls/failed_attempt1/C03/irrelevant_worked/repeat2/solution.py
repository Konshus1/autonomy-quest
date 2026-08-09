import re
import copy

class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self._validate_data()

    def _validate_data(self):
        if not isinstance(self._data, dict) or 'title' not in self._data or 'items' not in self._data:
            raise ValueError('Invalid workspace data')
        if not isinstance(self._data['title'], str):
            raise ValueError('Title must be a string')
        if not isinstance(self._data['items'], list):
            raise ValueError('Items must be a list')
        self._validate_items(self._data['items'])

    def _validate_items(self, items):
        seen_ids = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('Item must be a dict')
            if 'id' not in item or not isinstance(item['id'], str):
                raise ValueError('Item id must be a string')
            if item['id'] in seen_ids:
                raise ValueError('Duplicate id')
            seen_ids.add(item['id'])
            if 'kind' not in item or item['kind'] not in ('section', 'group', 'note', 'reference'):
                raise ValueError('Invalid kind')
            if 'title' not in item or not isinstance(item['title'], str):
                raise ValueError('Title must be a string')
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
                if 'unresolved' not in item or not isinstance(item['unresolved'], list) or not all(isinstance(m, str) for m in item['unresolved']):
                    raise ValueError('Reference unresolved must be list of strings')

    def total_word_count(self):
        return self._count_words(self._data['items'])

    def _count_words(self, items):
        total = 0
        for item in items:
            if item['kind'] == 'note':
                total += len(re.findall(r'\b\w+\b', item['text']))
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
                for m in re.findall(r'\[\[\?([^\]]+)\]\]', item['text']):
                    markers.add(m)
            elif item['kind'] == 'reference':
                markers.update(item['unresolved'])
            elif item['kind'] in ('section', 'group'):
                self._collect_markers(item['items'], markers)

    def outline(self):
        lines = [self._data['title']]
        self._render_items(self._data['items'], 0, lines)
        return '\n'.join(lines)

    def _render_items(self, items, depth, lines):
        for item in items:
            lines.append('  ' * depth + f"{item['kind']}: {item['title']}")
            if item['kind'] in ('section', 'group'):
                self._render_items(item['items'], depth + 1, lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError('IDs must be strings')
        # Find item and its parent
        item, parent, parent_items = self._find_item(self._data['items'], item_id)
        if item is None:
            raise ValueError('Item not found')
        # Determine destination container
        if destination_id == 'workspace':
            dest_items = self._data['items']
        else:
            dest_item, dest_parent, dest_parent_items = self._find_item(self._data['items'], destination_id)
            if dest_item is None:
                raise ValueError('Destination not found')
            if dest_item['kind'] not in ('section', 'group'):
                raise ValueError('Destination is not a container')
            dest_items = dest_item['items']
        # Validate index
        if index is None:
            index = len(dest_items)
        else:
            if not isinstance(index, int) or index < 0 or index > len(dest_items):
                raise ValueError('Invalid index')
        # Prevent moving into itself or descendant
        if destination_id != 'workspace':
            # Check if destination is inside the moving subtree
            if self._is_descendant(item, destination_id):
                raise ValueError('Cannot move into itself or descendant')
        # Perform move
        # Remove from original location
        parent_items.remove(item)
        # Insert at destination
        dest_items.insert(index, item)

    def _find_item(self, items, item_id):
        for i, item in enumerate(items):
            if item['id'] == item_id:
                return item, None, items
            if item['kind'] in ('section', 'group'):
                found = self._find_item(item['items'], item_id)
                if found[0] is not None:
                    return found
        return None, None, None

    def _is_descendant(self, container, item_id):
        # Check if item_id is inside container's subtree
        if container['kind'] not in ('section', 'group'):
            return False
        for child in container['items']:
            if child['id'] == item_id:
                return True
            if self._is_descendant(child, item_id):
                return True
        return False

    def to_data(self):
        return copy.deepcopy(self._data)
