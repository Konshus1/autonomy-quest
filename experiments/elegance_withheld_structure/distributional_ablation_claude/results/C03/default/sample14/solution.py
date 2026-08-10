import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def to_data(self):
        return copy.deepcopy(self._data)

    def _walk(self, items, depth=0):
        for item in items:
            yield item, depth
            if item.get("kind") in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    def total_word_count(self):
        total = 0
        for item, _ in self._walk(self._data["items"]):
            if item["kind"] == "note":
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item, _ in self._walk(self._data["items"]):
            if item["kind"] == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                found.update(item["unresolved"])
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]
        for item, depth in self._walk(self._data["items"], 1):
            lines.append("  " * depth + "{}: {}".format(item["kind"], item["title"]))
        return "\n".join(lines)

    def _find(self, item_id):
        stack = [self._data["items"]]
        while stack:
            items = stack.pop()
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    return items, i, item
                if item.get("kind") in _CONTAINER_KINDS:
                    stack.append(item["items"])
        return None

    def _subtree_contains(self, container, target):
        if container.get("kind") not in _CONTAINER_KINDS:
            return False
        return any(it is target for it, _ in self._walk(container["items"]))

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        found = self._find(item_id)
        if found is None:
            raise ValueError("no item with id {!r}".format(item_id))
        source_items, source_index, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError("no item with id {!r}".format(destination_id))
            _, _, dest = dest_found
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError("{!r} is not a container".format(destination_id))
            if dest is item or self._subtree_contains(item, dest):
                raise ValueError(
                    "cannot move a container into itself or its descendants"
                )
            dest_items = dest["items"]

        if index is not None:
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index <= len(dest_items)
            ):
                raise ValueError("index out of range")

        source_items.pop(source_index)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)
