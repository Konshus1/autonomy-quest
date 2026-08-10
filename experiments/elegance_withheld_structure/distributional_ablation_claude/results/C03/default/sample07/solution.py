"""Workspace model: sections, notes, references, and nested groups."""

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

    def _walk(self, items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"])

    def total_word_count(self):
        total = 0
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                found.update(item["unresolved"])
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]

        def render(items, depth):
            for item in items:
                lines.append("  " * depth + f"{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    render(item["items"], depth + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def _locate(self, item_id):
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

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        located = self._locate(item_id)
        if located is None:
            raise ValueError(f"no item with id {item_id!r}")
        source_list, source_index, item = located

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest_hit = self._locate(destination_id)
            if dest_hit is None:
                raise ValueError(f"no destination with id {destination_id!r}")
            dest_item = dest_hit[2]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"{destination_id!r} is not a container")
            subtree_ids = {node["id"] for node in self._walk([item])}
            if destination_id in subtree_ids:
                raise ValueError("cannot move a container into itself or its descendants")
            dest_list = dest_item["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_list):
                raise ValueError(f"index {index} out of range")

        source_list.pop(source_index)
        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)
