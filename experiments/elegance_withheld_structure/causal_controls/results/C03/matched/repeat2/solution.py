import copy
import re

class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self._build_index()

    def _build_index(self):
        self._index = {}
        self._parents = {}
        self._children = {}
        self._walk(self._data, None)

    def _walk(self, node, parent):
        if 'id' in node:
            self._index[node['id']] = node
            self._parents[node['id']] = parent
            self._children[node['id']] = []
        if node.get('kind') in ('workspace', 'section', 'group'):
            for child in node.get('items', []):
                self._children[node['id']].append(child['id'])
                self._walk(child, node['id'])

    def total_word_count(self):
        return self._count_words(self._data)

    def _count_words(self, node):
        kind = node.get('kind')
        if kind == 'note':
            text = node.get('text', '')
            # Remove marker tokens
            text_no_markers = re.sub(r'\[\?[^\]]+\]', '', text)
            return len(re.findall(r'\b\w+\b', text_no_markers))
        elif kind == 'reference':
            return node.get('word_count', 0)
        else:  # workspace, section, group
            total = 0
            for child in node.get('items', []):
                total += self._count_words(child)
            return total

    def unresolved_markers(self):
        markers = set()
        self._collect_markers(self._data, markers)
        return sorted(markers)

    def _collect_markers(self, node, markers):
        kind = node.get('kind')
        if kind == 'note':
            text = node.get('text', '')
            for m in re.findall(r'\[\?([^\]]+)\]', text):
                markers.add(m)
        elif kind == 'reference':
            for m in node.get('unresolved', []):
                markers.add(m)
        else:
            for child in node.get('items', []):
                self._collect_markers(child, markers)

    def outline(self):
        lines = []
        self._build_outline(self._data, 0, lines)
        return '\n'.join(lines)

    def _build_outline(self, node, depth, lines):
        if node.get('kind') == 'workspace':
            lines.append(node.get('title', ''))
            for child in node.get('items', []):
                self._build_outline(child, 0, lines)
        else:
            indent = '  ' * depth
            lines.append(f"{indent}{node.get('kind')}: {node.get('title', '')}")
            if node.get('kind') in ('section', 'group'):
                for child in node.get('items', []):
                    self._build_outline(child, depth + 1, lines)

    def move(self, item_id, destination_id, index=None):
        # Validate ids are strings
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("IDs must be strings")

        # Find item and destination
        if item_id not in self._index:
            raise ValueError("Item not found")
        item = self._index[item_id]

        if destination_id == 'workspace':
            dest = self._data
            dest_kind = 'workspace'
        else:
            if destination_id not in self._index:
                raise ValueError("Destination not found")
            dest = self._index[destination_id]
            dest_kind = dest.get('kind')

        # Destination must be container
        if dest_kind not in ('workspace', 'section', 'group'):
            raise ValueError("Destination is not a container")

        # Cannot move into itself or its descendants
        if self._is_descendant(item_id, destination_id):
            raise ValueError("Cannot move into itself or descendant")

        # Get current parent and siblings
        parent_id = self._parents[item_id]
        if parent_id is None:
            parent = self._data
        else:
            parent = self._index[parent_id]
        siblings = parent.get('items', [])
        old_index = next(i for i, c in enumerate(siblings) if c['id'] == item_id)

        # Remove from current location
        del siblings[old_index]

        # Determine insertion index
        dest_items = dest.get('items', [])
        if index is None:
            insert_index = len(dest_items)
        else:
            if not isinstance(index, int) or index < 0 or index > len(dest_items):
                # Restore? Actually we haven't mutated yet, but we already removed. Need to restore.
                # Better to validate before removal.
                # We'll re-add and raise.
                siblings.insert(old_index, item)
                raise ValueError("Index out of range")
            insert_index = index

        # Insert into destination
        dest_items.insert(insert_index, item)

        # Update index and parents
        self._parents[item_id] = destination_id if destination_id != 'workspace' else None
        # Rebuild children lists for affected nodes
        self._rebuild_children()

    def _is_descendant(self, ancestor_id, node_id):
        # Returns True if node_id is ancestor of ancestor_id (i.e., moving into descendant)
        cur = self._parents.get(ancestor_id)
        while cur is not None:
            if cur == node_id:
                return True
            cur = self._parents.get(cur)
        return False

    def _rebuild_children(self):
        self._children = {}
        self._walk(self._data, None)

    def to_data(self):
        return copy.deepcopy(self._data)
