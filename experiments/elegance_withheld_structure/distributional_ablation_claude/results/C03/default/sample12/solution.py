"""In-memory workspace of sections, groups, notes, and references.

Exports Workspace(data) per the frozen public contract; standard library only.
"""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ------------------------------------------------------------------
    # Summaries

    def total_word_count(self):
        total = 0
        for item in self._iter_items():
            if item["kind"] == "note":
                # Marker syntax and its identifier must not count as words.
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
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

    # ------------------------------------------------------------------
    # Rendering

    def outline(self):
        lines = [self._data["title"]]

        def walk(items, level):
            for item in items:
                lines.append("  " * level + "%s: %s" % (item["kind"], item["title"]))
                if item["kind"] in _CONTAINER_KINDS:
                    walk(item["items"], level + 1)

        walk(self._data["items"], 0)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Mutation

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        located = self._find(item_id)
        if located is None:
            raise ValueError("no item with id %r" % (item_id,))
        parent_items, position, item = located

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_located = self._find(destination_id)
            if dest_located is None:
                raise ValueError("no destination with id %r" % (destination_id,))
            dest_item = dest_located[2]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination %r is not a container" % (destination_id,))
            if dest_item is item or destination_id in self._descendant_ids(item):
                raise ValueError(
                    "cannot move %r into itself or its own contents" % (item_id,)
                )
            dest_items = dest_item["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_items):
                raise ValueError("index %r out of range" % (index,))

        # All validation passed; mutate atomically.
        parent_items.pop(position)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    # ------------------------------------------------------------------
    # Export

    def to_data(self):
        return copy.deepcopy(self._data)

    # ------------------------------------------------------------------
    # Internals

    def _iter_items(self):
        stack = list(reversed(self._data["items"]))
        while stack:
            item = stack.pop()
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                stack.extend(reversed(item["items"]))

    def _find(self, target_id):
        def search(items):
            for i, item in enumerate(items):
                if item["id"] == target_id:
                    return items, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    hit = search(item["items"])
                    if hit is not None:
                        return hit
            return None

        return search(self._data["items"])

    def _descendant_ids(self, item):
        ids = set()
        if item["kind"] in _CONTAINER_KINDS:
            def collect(items):
                for child in items:
                    ids.add(child["id"])
                    if child["kind"] in _CONTAINER_KINDS:
                        collect(child["items"])

            collect(item["items"])
        return ids
