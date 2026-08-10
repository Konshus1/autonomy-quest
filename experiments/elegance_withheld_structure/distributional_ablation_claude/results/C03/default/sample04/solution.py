"""In-memory workspace trees: word counts, unresolved markers, outlines, and moves."""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def to_data(self):
        return copy.deepcopy(self._data)

    def _iter_items(self, items=None, depth=1):
        """Yield (item, parent_list, index, depth) for every item in order."""
        if items is None:
            items = self._data["items"]
        for i, item in enumerate(items):
            yield item, items, i, depth
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._iter_items(item["items"], depth + 1)

    def total_word_count(self):
        total = 0
        for item, _, _, _ in self._iter_items():
            if item["kind"] == "note":
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item, _, _, _ in self._iter_items():
            if item["kind"] == "note":
                markers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self):
        lines = [self._data["title"]]
        for item, _, _, depth in self._iter_items():
            lines.append("  " * depth + "{}: {}".format(item["kind"], item["title"]))
        return "\n".join(lines)

    def _subtree_contains(self, item, target_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        for child in item["items"]:
            if child["id"] == target_id or self._subtree_contains(child, target_id):
                return True
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")

        moved = None
        destination = None
        for item, parent_list, i, _ in self._iter_items():
            if item["id"] == item_id:
                moved = (item, parent_list, i)
            if item["id"] == destination_id:
                destination = item

        if moved is None:
            raise ValueError("no item with id {!r}".format(item_id))
        item, source_list, source_index = moved

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            if destination is None:
                raise ValueError("no item with id {!r}".format(destination_id))
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination {!r} is not a container".format(destination_id))
            if destination_id == item_id or self._subtree_contains(item, destination_id):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_items = destination["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_items):
                raise ValueError("index {} out of range".format(index))

        source_list.pop(source_index)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)
