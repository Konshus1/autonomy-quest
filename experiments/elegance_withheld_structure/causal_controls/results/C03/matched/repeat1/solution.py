import copy
import re

class _Node:
    def __init__(self, data, parent=None):
        self.id = data['id']
        self.kind = data['kind']
        self.title = data.get('title', '')
        self.parent = parent
        self.children = []
        self.text = data.get('text', '')
        self.label = data.get('label', '')
        self.word_count = data.get('word_count', 0)
        self.unresolved = list(data.get('unresolved', []))
        if self.kind in ('section', 'group'):
            for child_data in data.get('items', []):
                self.children.append(_Node(child_data, self))

    def is_container(self):
        return self.kind in ('section', 'group')

    def total_word_count(self):
        if self.kind == 'note':
            return len(re.findall(r'\b\w+\b', self.text))
        elif self.kind == 'reference':
            return self.word_count
        else:
            return sum(child.total_word_count() for child in self.children)

    def collect_markers(self, markers):
        if self.kind == 'note':
            for m in re.findall(r'\[\[\?([^\]]+)\]\]', self.text):
                markers.add(m)
        elif self.kind == 'reference':
            for m in self.unresolved:
                markers.add(m)
        else:
            for child in self.children:
                child.collect_markers(markers)

    def outline_lines(self, depth):
        lines = []
        if self.kind != 'workspace':
            lines.append('  ' * depth + f"{self.kind}: {self.title}")
        for child in self.children:
            lines.extend(child.outline_lines(depth + 1))
        return lines

    def find(self, item_id):
        if self.id == item_id:
            return self
        for child in self.children:
            found = child.find(item_id)
            if found:
                return found
        return None

    def contains(self, node):
        if self is node:
            return True
        for child in self.children:
            if child.contains(node):
                return True
        return False

    def remove_child(self, child):
        self.children.remove(child)
        child.parent = None

    def insert_child(self, index, child):
        self.children.insert(index, child)
        child.parent = self

class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self.root = _Node({'id': 'workspace', 'kind': 'workspace', 'title': self._data['title'], 'items': self._data['items']})

    def total_word_count(self):
        return self.root.total_word_count()

    def unresolved_markers(self):
        markers = set()
        self.root.collect_markers(markers)
        return sorted(markers)

    def outline(self):
        lines = [self._data['title']]
        for child in self.root.children:
            lines.extend(child.outline_lines(1))
        return '\n'.join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError('IDs must be strings')
        item = self.root.find(item_id)
        if item is None:
            raise ValueError('Item not found')
        if destination_id == 'workspace':
            dest = self.root
        else:
            dest = self.root.find(destination_id)
            if dest is None:
                raise ValueError('Destination not found')
            if not dest.is_container():
                raise ValueError('Destination is not a container')
        if item is dest or dest.contains(item):
            raise ValueError('Invalid move: would create cycle')
        if index is None:
            idx = len(dest.children)
        else:
            if not isinstance(index, int) or index < 0 or index > len(dest.children):
                raise ValueError('Index out of range')
            idx = index
        old_parent = item.parent
        old_parent.remove_child(item)
        dest.insert_child(idx, item)

    def to_data(self):
        def serialize(node):
            d = {'id': node.id, 'kind': node.kind, 'title': node.title}
            if node.kind == 'note':
                d['text'] = node.text
            elif node.kind == 'reference':
                d['label'] = node.label
                d['word_count'] = node.word_count
                d['unresolved'] = list(node.unresolved)
            elif node.kind in ('section', 'group'):
                d['items'] = [serialize(child) for child in node.children]
            return d
        data = {'title': self._data['title'], 'items': [serialize(child) for child in self.root.children]}
        return copy.deepcopy(data)
