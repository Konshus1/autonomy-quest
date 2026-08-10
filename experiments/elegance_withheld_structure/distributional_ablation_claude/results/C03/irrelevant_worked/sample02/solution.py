"""Workspace tree with word counts, unresolved markers, outline, and moves."""

import copy
import re

_MARKER = re.compile(r"\[\[\?(\w+)\]\]")
_WORD = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def total_word_count(self):
        return sum(self._word_count(item) for item in self._data["items"])

    def unresolved_markers(self):
        found = set()
        for item in self._data["items"]:
            self._collect_markers(item, found)
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]
        self._render(self._data["items"], 1, lines)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")
        found = self._find(item_id, self._data["items"])
        if found is None:
            raise ValueError("no item with id %r" % item_id)
        parent_items, pos, item = found
        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_found = self._find(destination_id, self._data["items"])
            if dest_found is None:
                raise ValueError("no destination with id %r" % destination_id)
            dest = dest_found[2]
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination %r is not a container" % destination_id)
            if dest is item or self._contains(item, dest):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_items = dest["items"]
        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_items):
                raise ValueError("index %d out of range" % index)
        del parent_items[pos]
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)

    @classmethod
    def _word_count(cls, item):
        kind = item["kind"]
        if kind == "note":
            return len(_WORD.findall(_MARKER.sub(" ", item["text"])))
        if kind == "reference":
            return item["word_count"]
        return sum(cls._word_count(child) for child in item["items"])

    @classmethod
    def _collect_markers(cls, item, found):
        kind = item["kind"]
        if kind == "note":
            found.update(_MARKER.findall(item["text"]))
        elif kind == "reference":
            found.update(item["unresolved"])
        else:
            for child in item["items"]:
                cls._collect_markers(child, found)

    @classmethod
    def _render(cls, items, depth, lines):
        for item in items:
            lines.append("  " * depth + "%s: %s" % (item["kind"], item["title"]))
            if item["kind"] in _CONTAINER_KINDS:
                cls._render(item["items"], depth + 1, lines)

    @classmethod
    def _find(cls, item_id, items):
        for i, item in enumerate(items):
            if item["id"] == item_id:
                return items, i, item
            if item["kind"] in _CONTAINER_KINDS:
                result = cls._find(item_id, item["items"])
                if result is not None:
                    return result
        return None

    @classmethod
    def _contains(cls, container, target):
        for child in container.get("items", ()):
            if child is target or cls._contains(child, target):
                return True
        return False
