import re
import copy

class Workspace:
    def __init__(self, data):
        if not isinstance(data, dict) or 'title' not in data or 'items' not in data:
            raise ValueError("Invalid workspace data")
        self._data = copy.deepcopy(data)
        self._validate_ids(self._data['items'])

    def _validate_ids(self, items):
        seen = set()
        def walk(items):
            for item in items:
                if not isinstance(item.get('id'), str):
                    raise ValueError("Non-string id")
                if item['id'] in seen:
                    raise ValueError("Duplicate id")
                seen.add(item['id'])
                if item['kind'] in ('section', 'group'):
                    walk(item.get('items', []))
        walk(items)

    def _find_item(self, item_id, items=None):
        if items is None:
            items = self._data['items']
        for item in items:
            if item['id'] == item_id:
                return item
            if item['kind'] in ('section', 'group'):
                found = self._find_item(item_id, item.get('items', []))
                if found is not None:
                    return found
        return None

    def _find_parent(self, item_id, items=None, parent=None):
        if items is None:
            items = self._data['items']
        for item in items:
            if item['id'] == item_id:
                return parent
            if item['kind'] in ('section', 'group'):
                found = self._find_parent(item_id, item.get('items', []), item)
                if found is not None:
                    return found
        return None

    def _is_descendant(self, ancestor_id, item_id):
        ancestor = self._find_item(ancestor_id)
        if ancestor is None:
            return False
        return self._find_item(item_id, ancestor.get('items', [])) is not None

    def total_word_count(self):
        return self._count_words(self._data['items'])

    def _count_words(self, items):
        total = 0
        for item in items:
            if item['kind'] == 'note':
                text = item.get('text', '')
                # Remove marker tokens before counting words
                text_no_markers = re.sub(r'\[\?[^\]]+\]', '', text)
                total += len(re.findall(r'\b\w+\b', text_no_markers))
            elif item['kind'] == 'reference':
                total += item.get('word_count', 0)
            elif item['kind'] in ('section', 'group'):
                total += self._count_words(item.get('items', []))
        return total

    def unresolved_markers(self):
        markers = set()
        self._collect_markers(self._data['items'], markers)
        return sorted(markers)

    def _collect_markers(self, items, markers):
        for item in items:
            if item['kind'] == 'note':
                text = item.get('text', '')
                for m in re.findall(r'\[\?([^\]]+)\]', text):
                    markers.add(m)
            elif item['kind'] == 'reference':
                for m in item.get('unresolved', []):
                    markers.add(m)
            elif item['kind'] in ('section', 'group'):
                self._collect_markers(item.get('items', []), markers)

    def outline(self):
        lines = [self._data['title']]
        self._render_items(self._data['items'], 0, lines)
        return '\n'.join(lines)

    def _render_items(self, items, depth, lines):
        for item in items:
            indent = '  ' * depth
            lines.append(f"{indent}{item['kind']}: {item['title']}")
            if item['kind'] in ('section', 'group'):
                self._render_items(item.get('items', []), depth + 1, lines)

    def move(self, item_id, destination_id, index=None):
        # Validate ids
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("Non-string id")
        item = self._find_item(item_id)
        if item is None:
            raise ValueError("Item not found")
        if destination_id == 'workspace':
            dest_items = self._data['items']
            dest_container = None
        else:
            dest = self._find_item(destination_id)
            if dest is None:
                raise ValueError("Destination not found")
            if dest['kind'] not in ('section', 'group'):
                raise ValueError("Destination not a container")
            dest_items = dest.get('items', [])
            dest_container = dest

        # Check cycle: moving container into itself or descendant
        if item['kind'] in ('section', 'group'):
            if destination_id == item_id or self._is_descendant(item_id, destination_id):
                raise ValueError("Invalid move: would create cycle")

        # Validate index
        if index is None:
            index = len(dest_items)
        else:
            if not isinstance(index, int) or index < 0 or index > len(dest_items):
                raise ValueError("Index out of range")

        # Remove from current parent
        parent = self._find_parent(item_id)
        if parent is None:
            src_items = self._data['items']
        else:
            src_items = parent.get('items', [])
        src_items.remove(item)

        # Insert into destination
        dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
