"""Workspace tree of sections, groups, notes, and references.

Provides word-count and unresolved-marker summaries, a deterministic
indented outline, and validated relocation of items.
"""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def _walk(self, items, depth=1):
        for item in items:
            yield item, depth
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    def total_word_count(self):
        total = 0
        for item, _ in self._walk(self._data["items"]):
            if item["kind"] == "note":
                # Replace markers with a space so words on either side of a
                # marker are not merged into one run.
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
        for item, depth in self._walk(self._data["items"]):
            lines.append("  " * depth + "{}: {}".format(item["kind"], item["title"]))
        return "\n".join(lines)

    def _find(self, item_id, items):
        for pos, item in enumerate(items):
            if item["id"] == item_id:
                return items, pos, item
            if item["kind"] in _CONTAINER_KINDS:
                hit = self._find(item_id, item["items"])
                if hit is not None:
                    return hit
        return None

    def _contains(self, item, target_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        return any(child["id"] == target_id
                   for child, _ in self._walk(item["items"]))

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        hit = self._find(item_id, self._data["items"])
        if hit is None:
            raise ValueError("unknown item id: {!r}".format(item_id))
        parent_items, pos, item = hit

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_hit = self._find(destination_id, self._data["items"])
            if dest_hit is None:
                raise ValueError("unknown destination id: {!r}".format(destination_id))
            dest = dest_hit[2]
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container: {!r}".format(destination_id))
            if dest["id"] == item["id"] or self._contains(item, destination_id):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_items = dest["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            # Insertion happens after removal, so bound against the
            # destination length as it will be at insertion time.
            effective_len = len(dest_items) - (1 if dest_items is parent_items else 0)
            if index < 0 or index > effective_len:
                raise ValueError("index out of range")

        parent_items.pop(pos)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
