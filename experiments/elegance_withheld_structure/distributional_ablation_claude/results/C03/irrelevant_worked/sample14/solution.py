"""Workspace model: sections, groups, notes, and references, with word-count
aggregation, unresolved-marker collection, outline rendering, and validated
relocation of items."""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")

_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def _walk(self, items=None, depth=1):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item, depth
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    def total_word_count(self):
        total = 0
        for item, _ in self._walk():
            if item["kind"] == "note":
                # Markers and their identifiers must not count as words.
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item, _ in self._walk():
            if item["kind"] == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                found.update(item["unresolved"])
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]
        for item, depth in self._walk():
            lines.append("  " * depth + "{}: {}".format(item["kind"], item["title"]))
        return "\n".join(lines)

    def _find(self, item_id):
        stack = [self._data["items"]]
        while stack:
            items = stack.pop()
            for position, item in enumerate(items):
                if item["id"] == item_id:
                    return items, position, item
                if item["kind"] in _CONTAINER_KINDS:
                    stack.append(item["items"])
        return None

    def _contains(self, container_item, target_id):
        for item, _ in self._walk(container_item["items"]):
            if item["id"] == target_id:
                return True
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        found = self._find(item_id)
        if found is None:
            raise ValueError("no item with id {!r}".format(item_id))
        source_list, position, item = found

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError("no item with id {!r}".format(destination_id))
            dest_item = dest_found[2]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("{!r} is not a container".format(destination_id))
            if dest_item is item or (
                item["kind"] in _CONTAINER_KINDS
                and self._contains(item, destination_id)
            ):
                raise ValueError(
                    "cannot move a container into itself or its descendants"
                )
            dest_list = dest_item["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_list):
                raise ValueError("index out of range")

        # All validation passed; mutate atomically.
        del source_list[position]
        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
