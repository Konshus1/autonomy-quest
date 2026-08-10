"""In-memory workspace model: sections, notes, references, and nested groups.

Uses only the Python standard library.
"""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ------------------------------------------------------------------
    # Traversal

    def _walk(self, items, depth):
        for item in items:
            yield item, depth
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    def _iter_all(self):
        yield from self._walk(self._data["items"], 1)

    # ------------------------------------------------------------------
    # Queries

    def total_word_count(self):
        total = 0
        for item, _ in self._iter_all():
            if item["kind"] == "note":
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item, _ in self._iter_all():
            if item["kind"] == "note":
                markers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self):
        lines = [self._data["title"]]
        for item, depth in self._iter_all():
            lines.append("  " * depth + "%s: %s" % (item["kind"], item["title"]))
        return "\n".join(lines)

    def to_data(self):
        return copy.deepcopy(self._data)

    # ------------------------------------------------------------------
    # Move

    def _find_with_parent(self, item_id):
        stack = [self._data["items"]]
        while stack:
            siblings = stack.pop()
            for pos, item in enumerate(siblings):
                if item["id"] == item_id:
                    return siblings, pos, item
                if item["kind"] in _CONTAINER_KINDS:
                    stack.append(item["items"])
        raise ValueError("no item with id %r" % (item_id,))

    def _subtree_contains(self, item, target_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        for descendant, _ in self._walk(item["items"], 0):
            if descendant["id"] == target_id:
                return True
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")

        parent_items, pos, item = self._find_with_parent(item_id)

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            _, _, dest = self._find_with_parent(destination_id)
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination %r is not a container" % (destination_id,))
            if destination_id == item_id or self._subtree_contains(item, destination_id):
                raise ValueError("cannot move a container into itself or its descendants")
            dest_items = dest["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_items):
                raise ValueError("index %d out of range" % index)

        parent_items.pop(pos)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)
