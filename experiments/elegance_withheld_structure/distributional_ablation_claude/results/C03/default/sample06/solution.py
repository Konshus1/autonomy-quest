import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    """A tree of sections, groups, notes, and references.

    The workspace deep-copies its input and never exposes internal
    state: ``to_data`` returns a fresh deep copy.
    """

    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ------------------------------------------------------------------
    # Traversal

    def _walk(self, items, depth):
        for item in items:
            yield item, depth
            if item.get("kind") in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    # ------------------------------------------------------------------
    # Queries

    def total_word_count(self):
        total = 0
        for item, _ in self._walk(self._data["items"], 1):
            kind = item["kind"]
            if kind == "note":
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
            elif kind == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        identifiers = set()
        for item, _ in self._walk(self._data["items"], 1):
            kind = item["kind"]
            if kind == "note":
                identifiers.update(_MARKER_RE.findall(item["text"]))
            elif kind == "reference":
                identifiers.update(item["unresolved"])
        return sorted(identifiers)

    def outline(self):
        lines = [self._data["title"]]
        for item, depth in self._walk(self._data["items"], 1):
            lines.append("  " * depth + "{}: {}".format(item["kind"], item["title"]))
        return "\n".join(lines)

    def to_data(self):
        return copy.deepcopy(self._data)

    # ------------------------------------------------------------------
    # Mutation

    def _find(self, items, item_id):
        """Return (containing_list, index, item) or None."""
        for i, item in enumerate(items):
            if item["id"] == item_id:
                return items, i, item
            if item.get("kind") in _CONTAINER_KINDS:
                found = self._find(item["items"], item_id)
                if found is not None:
                    return found
        return None

    def _subtree_contains(self, item, target_id):
        if item.get("kind") not in _CONTAINER_KINDS:
            return False
        for descendant, _ in self._walk(item["items"], 0):
            if descendant["id"] == target_id:
                return True
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")

        found = self._find(self._data["items"], item_id)
        if found is None:
            raise ValueError("no item with id {!r}".format(item_id))
        source_list, source_index, item = found

        if destination_id == "workspace":
            destination_items = self._data["items"]
        else:
            dest_found = self._find(self._data["items"], destination_id)
            if dest_found is None:
                raise ValueError("no destination with id {!r}".format(destination_id))
            destination = dest_found[2]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError(
                    "destination {!r} is not a container".format(destination_id)
                )
            if destination_id == item_id or self._subtree_contains(item, destination_id):
                raise ValueError("cannot move a container into itself or a descendant")
            destination_items = destination["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(destination_items):
                raise ValueError("index {} out of range".format(index))

        # All validation passed; mutate atomically.
        source_list.pop(source_index)
        if index is None:
            destination_items.append(item)
        else:
            destination_items.insert(index, item)
