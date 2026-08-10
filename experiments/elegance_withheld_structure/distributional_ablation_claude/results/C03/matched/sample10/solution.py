"""Workspace model: sections, notes, references, and nested groups."""

import copy
import re

_MARKER_RE = re.compile(r"\\[\\[\\?([^\\[\\]]+?)\\]\\]")
_WORD_RE = re.compile(r"\\b\\w+\\b")

_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def _walk(self, items, level=1):
        for item in items:
            yield item, level
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], level + 1)

    def total_word_count(self):
        total = 0
        for item, _ in self._walk(self._data["items"]):
            if item["kind"] == "note":
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item, _ in self._walk(self._data["items"]):
            if item["kind"] == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                found.update(item["unresolved"])
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]
        for item, level in self._walk(self._data["items"]):
            lines.append("  " * level + f"{item['kind']}: {item['title']}")
        return "\\n".join(lines)

    def _locate(self, item_id):
        stack = [self._data["items"]]
        while stack:
            items = stack.pop()
            for pos, item in enumerate(items):
                if item["id"] == item_id:
                    return items, pos, item
                if item["kind"] in _CONTAINER_KINDS:
                    stack.append(item["items"])
        return None

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")
        located = self._locate(item_id)
        if located is None:
            raise ValueError(f"unknown item id: {item_id!r}")
        source_items, pos, item = located

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_located = self._locate(destination_id)
            if dest_located is None:
                raise ValueError(f"unknown destination id: {destination_id!r}")
            dest = dest_located[2]
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"destination is not a container: {destination_id!r}")
            subtree_ids = {sub["id"] for sub, _ in self._walk([item])}
            if destination_id in subtree_ids:
                raise ValueError("cannot move an item into itself or a descendant")
            dest_items = dest["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_items):
                raise ValueError(f"index out of range: {index}")

        source_items.pop(pos)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
