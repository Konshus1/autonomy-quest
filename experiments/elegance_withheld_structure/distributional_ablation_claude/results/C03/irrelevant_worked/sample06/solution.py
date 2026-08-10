"""Workspace tree with word counts, unresolved markers, outline, and validated moves."""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def total_word_count(self):
        return sum(_item_word_count(item) for item in self._data["items"])

    def unresolved_markers(self):
        found = set()
        for item in self._data["items"]:
            _collect_markers(item, found)
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]
        for item in self._data["items"]:
            _outline_item(item, 1, lines)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        found = self._find(item_id)
        if found is None:
            raise ValueError(f"no item with id {item_id!r}")
        source_items, source_index, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError(f"no item with id {destination_id!r}")
            _, _, dest = dest_found
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"destination {destination_id!r} is not a container")
            if destination_id == item_id or _subtree_contains(item, destination_id):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_items = dest["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_items):
                raise ValueError(f"index {index} out of range")

        del source_items[source_index]
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)

    def _find(self, item_id):
        def search(items):
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    return items, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    hit = search(item["items"])
                    if hit is not None:
                        return hit
            return None

        return search(self._data["items"])


def _item_word_count(item):
    kind = item["kind"]
    if kind == "note":
        # Markers become spaces so their identifiers never merge with or count as words.
        return len(_WORD_RE.findall(_MARKER_RE.sub(" ", item["text"])))
    if kind == "reference":
        return item["word_count"]
    return sum(_item_word_count(child) for child in item["items"])


def _collect_markers(item, found):
    kind = item["kind"]
    if kind == "note":
        found.update(_MARKER_RE.findall(item["text"]))
    elif kind == "reference":
        found.update(item["unresolved"])
    else:
        for child in item["items"]:
            _collect_markers(child, found)


def _outline_item(item, depth, lines):
    lines.append("  " * depth + f"{item['kind']}: {item['title']}")
    if item["kind"] in _CONTAINER_KINDS:
        for child in item["items"]:
            _outline_item(child, depth + 1, lines)


def _subtree_contains(item, target_id):
    for child in item.get("items", []):
        if child["id"] == target_id or _subtree_contains(child, target_id):
            return True
    return False
