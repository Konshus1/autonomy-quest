"""Workspace model: sections, notes, references, and nested groups.

Standard library only. See Workspace for the public API.
"""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+?)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ---- queries -------------------------------------------------------

    def total_word_count(self):
        total = 0
        for item in self._iter_items():
            if item["kind"] == "note":
                # Markers are replaced by a space so their identifiers are
                # never counted and surrounding words do not merge.
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item in self._iter_items():
            if item["kind"] == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                found.update(item["unresolved"])
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]

        def walk(items, depth):
            for item in items:
                lines.append("  " * depth + "%s: %s" % (item["kind"], item["title"]))
                if item["kind"] in _CONTAINER_KINDS:
                    walk(item["items"], depth + 1)

        walk(self._data["items"], 1)
        return "\n".join(lines)

    def to_data(self):
        return copy.deepcopy(self._data)

    # ---- mutation ------------------------------------------------------

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        found = self._find(item_id)
        if found is None:
            raise ValueError("no item with id %r" % (item_id,))
        source_items, position, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError("no item with id %r" % (destination_id,))
            dest = dest_found[2]
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination %r is not a container" % (destination_id,))
            dest_items = dest["items"]
            if dest["id"] == item["id"] or self._subtree_contains(item, destination_id):
                raise ValueError("cannot move a container into itself or its contents")

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_items):
                raise ValueError("index %d out of range for destination" % index)

        # All validation passed; mutate atomically.
        source_items.pop(position)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    # ---- internals -----------------------------------------------------

    def _iter_items(self, items=None):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._iter_items(item["items"])

    def _find(self, item_id):
        def walk(items):
            for position, item in enumerate(items):
                if item["id"] == item_id:
                    return items, position, item
                if item["kind"] in _CONTAINER_KINDS:
                    hit = walk(item["items"])
                    if hit is not None:
                        return hit
            return None

        return walk(self._data["items"])

    def _subtree_contains(self, item, target_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        return any(d["id"] == target_id for d in self._iter_items(item["items"]))
