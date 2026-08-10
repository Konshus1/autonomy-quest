import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    """A tree of sections, groups, notes, and references.

    The workspace owns a deep copy of the data it is given; ``to_data``
    hands back deep copies, so external code can never mutate internal
    state through shared references.
    """

    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ------------------------------------------------------------------
    # Traversal

    def _walk(self, items):
        for item in items:
            yield item
            if item.get("kind") in _CONTAINER_KINDS:
                yield from self._walk(item["items"])

    def _find_item(self, item_id):
        for item in self._walk(self._data["items"]):
            if item["id"] == item_id:
                return item
        raise ValueError(f"no item with id {item_id!r}")

    def _find_parent_list(self, item_id):
        """Return (containing_list, position) for the item with item_id."""
        stack = [self._data["items"]]
        while stack:
            items = stack.pop()
            for pos, item in enumerate(items):
                if item["id"] == item_id:
                    return items, pos
                if item.get("kind") in _CONTAINER_KINDS:
                    stack.append(item["items"])
        raise ValueError(f"no item with id {item_id!r}")

    def _subtree_contains(self, item, target_id):
        if item.get("kind") not in _CONTAINER_KINDS:
            return False
        return any(child["id"] == target_id for child in self._walk(item["items"]))

    # ------------------------------------------------------------------
    # Aggregate queries

    def total_word_count(self):
        total = 0
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                # Markers and their identifiers are not words; replace with
                # a space so surrounding words stay separated.
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

        def emit(items, depth):
            for item in items:
                lines.append("  " * depth + f"{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    emit(item["items"], depth + 1)

        emit(self._data["items"], 1)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Mutation

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")

        source_list, pos = self._find_parent_list(item_id)
        item = source_list[pos]

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest = self._find_item(destination_id)
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError(
                    f"destination {destination_id!r} is not a container"
                )
            if destination_id == item_id or self._subtree_contains(item, destination_id):
                raise ValueError(
                    "cannot move a container into itself or its descendants"
                )
            dest_list = dest["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_list):
                raise ValueError(f"index {index} out of range")

        # All validation passed; mutate. The item object (with its whole
        # subtree) is moved intact.
        del source_list[pos]
        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)

    # ------------------------------------------------------------------
    # Export

    def to_data(self):
        return copy.deepcopy(self._data)
