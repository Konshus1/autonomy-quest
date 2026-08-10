"""Workspace composite: sections/groups contain notes, references, and groups."""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ---- queries -------------------------------------------------

    def total_word_count(self):
        total = 0
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                for marker in item["unresolved"]:
                    full = _MARKER_RE.fullmatch(marker)
                    found.add(full.group(1) if full else marker)
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]

        def render(items, depth):
            for item in items:
                lines.append("  " * depth + "%s: %s" % (item["kind"], item["title"]))
                if item["kind"] in _CONTAINER_KINDS:
                    render(item["items"], depth + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def to_data(self):
        return copy.deepcopy(self._data)

    # ---- mutation ------------------------------------------------

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        located = self._locate(item_id)
        if located is None:
            raise ValueError("no item with id %r" % item_id)
        parent_list, position, item = located

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest_located = self._locate(destination_id)
            if dest_located is None:
                raise ValueError("no destination with id %r" % destination_id)
            dest_item = dest_located[2]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination %r is not a container" % destination_id)
            if dest_item is item or self._contains(item, destination_id):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_list = dest_item["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_list):
                raise ValueError("index %r out of range" % index)

        # All checks passed; the mutation below cannot fail partway.
        parent_list.pop(position)
        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)

    # ---- internals -----------------------------------------------

    def _walk(self, items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"])

    def _locate(self, item_id, items=None):
        if items is None:
            items = self._data["items"]
        for position, item in enumerate(items):
            if item["id"] == item_id:
                return items, position, item
            if item["kind"] in _CONTAINER_KINDS:
                found = self._locate(item_id, item["items"])
                if found is not None:
                    return found
        return None

    def _contains(self, item, target_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        return any(child["id"] == target_id for child in self._walk(item["items"]))
