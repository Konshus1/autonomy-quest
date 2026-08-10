import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(.+?)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    """A tree of sections, groups, notes, and references."""

    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def to_data(self):
        return copy.deepcopy(self._data)

    # ------------------------------------------------------------------
    # Traversal helpers
    # ------------------------------------------------------------------

    def _walk(self, items=None):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"])

    def _locate(self, item_id, items=None):
        """Return (parent_list, index, item) for item_id, or None."""
        if items is None:
            items = self._data["items"]
        for i, item in enumerate(items):
            if item["id"] == item_id:
                return items, i, item
            if item["kind"] in _CONTAINER_KINDS:
                found = self._locate(item_id, item["items"])
                if found is not None:
                    return found
        return None

    @staticmethod
    def _subtree_contains(container, target):
        """True if target is an item (by identity) inside container's subtree."""
        if container["kind"] not in _CONTAINER_KINDS:
            return False
        stack = list(container["items"])
        while stack:
            item = stack.pop()
            if item is target:
                return True
            if item["kind"] in _CONTAINER_KINDS:
                stack.extend(item["items"])
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def total_word_count(self):
        total = 0
        for item in self._walk():
            if item["kind"] == "note":
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item in self._walk():
            if item["kind"] == "note":
                markers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self):
        lines = [self._data["title"]]

        def render(items, depth):
            for item in items:
                lines.append("  " * depth + f"{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    render(item["items"], depth + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")

        located = self._locate(item_id)
        if located is None:
            raise ValueError(f"no item with id {item_id!r}")
        parent_items, position, item = located

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_located = self._locate(destination_id)
            if dest_located is None:
                raise ValueError(f"no destination with id {destination_id!r}")
            dest_item = dest_located[2]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError(
                    f"destination {destination_id!r} is not a container"
                )
            if dest_item is item or self._subtree_contains(item, dest_item):
                raise ValueError(
                    "cannot move a container into itself or its descendants"
                )
            dest_items = dest_item["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_items):
                raise ValueError(f"index {index} out of range")

        # All validation passed; mutate.
        parent_items.pop(position)
        if index is None:
            dest_items.append(item)
        else:
            # For same-list moves, list.insert's clamping keeps the intended
            # insertion-index semantics after the removal shifted positions.
            dest_items.insert(index, item)
