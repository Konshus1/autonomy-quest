"""Workspace of sections, notes, references, and nested groups."""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ---- queries ----

    def total_word_count(self):
        total = 0
        for item, _level in self._walk():
            kind = item["kind"]
            if kind == "note":
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
            elif kind == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item, _level in self._walk():
            kind = item["kind"]
            if kind == "note":
                markers.update(_MARKER_RE.findall(item["text"]))
            elif kind == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self):
        lines = [self._data["title"]]
        for item, level in self._walk():
            lines.append("%s%s: %s" % ("  " * level, item["kind"], item["title"]))
        return "\n".join(lines)

    def to_data(self):
        return copy.deepcopy(self._data)

    # ---- mutation ----

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        found = self._find_with_parent(item_id)
        if found is None:
            raise ValueError("no item with id %r" % item_id)
        parent_items, pos, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest = self._find(destination_id)
            if dest is None:
                raise ValueError("no destination with id %r" % destination_id)
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination %r is not a container" % destination_id)
            if dest is item or self._subtree_contains(item, destination_id):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_items = dest["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_items):
                raise ValueError("index %r out of range" % index)

        del parent_items[pos]
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    # ---- helpers ----

    def _walk(self, items=None, level=1):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item, level
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], level + 1)

    def _find(self, item_id):
        for item, _level in self._walk():
            if item["id"] == item_id:
                return item
        return None

    def _find_with_parent(self, item_id, items=None):
        if items is None:
            items = self._data["items"]
        for pos, item in enumerate(items):
            if item["id"] == item_id:
                return items, pos, item
            if item["kind"] in _CONTAINER_KINDS:
                found = self._find_with_parent(item_id, item["items"])
                if found is not None:
                    return found
        return None

    def _subtree_contains(self, item, item_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        for inner, _level in self._walk(item["items"]):
            if inner["id"] == item_id:
                return True
        return False
