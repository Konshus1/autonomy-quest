"""Workspace model: sections, notes, references, and nested groups.

Standard library only. All state is held in a plain-dict tree that is
deep-copied on the way in and on the way out.
"""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = frozenset({"section", "group"})


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def _walk(self, items=None, depth=0):
        """Yield (item, depth) in document order; top-level items have depth 0."""
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item, depth
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    def _locate(self, target_id, items=None):
        """Return (parent_list, index, item) for target_id, or None."""
        if items is None:
            items = self._data["items"]
        for i, item in enumerate(items):
            if item["id"] == target_id:
                return items, i, item
            if item["kind"] in _CONTAINER_KINDS:
                found = self._locate(target_id, item["items"])
                if found is not None:
                    return found
        return None

    @staticmethod
    def _contains(ancestor, target):
        """True if target is a descendant of ancestor."""
        if ancestor["kind"] not in _CONTAINER_KINDS:
            return False
        stack = list(ancestor["items"])
        while stack:
            node = stack.pop()
            if node is target:
                return True
            if node["kind"] in _CONTAINER_KINDS:
                stack.extend(node["items"])
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def total_word_count(self):
        total = 0
        for item, _ in self._walk():
            if item["kind"] == "note":
                # Markers and their identifiers never count as words.
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
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
            lines.append("  " * (depth + 1) + f"{item['kind']}: {item['title']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def move(self, item_id, destination_id, index=None):
        # All validation happens before any mutation so a failed move
        # leaves the workspace exactly unchanged.
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        located = self._locate(item_id)
        if located is None:
            raise ValueError(f"no item with id {item_id!r}")
        source_list, position, item = located

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest = self._locate(destination_id)
            if dest is None:
                raise ValueError(f"no item with id {destination_id!r}")
            container = dest[2]
            if container["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"{destination_id!r} is not a container")
            if container is item or self._contains(item, container):
                raise ValueError(
                    "cannot move an item into itself or its own subtree"
                )
            dest_list = container["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_list):
                raise ValueError(f"index {index} out of range")

        del source_list[position]
        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def to_data(self):
        return copy.deepcopy(self._data)
