import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


def _iter_items(items):
    for item in items:
        yield item
        if item["kind"] in _CONTAINER_KINDS:
            yield from _iter_items(item["items"])


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def to_data(self):
        return copy.deepcopy(self._data)

    def total_word_count(self):
        total = 0
        for item in _iter_items(self._data["items"]):
            if item["kind"] == "note":
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item in _iter_items(self._data["items"]):
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

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        located = self._locate(item_id)
        if located is None:
            raise ValueError(f"no item with id {item_id!r}")
        source_items, source_index, item = located

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_located = self._locate(destination_id)
            if dest_located is None:
                raise ValueError(f"no item with id {destination_id!r}")
            dest = dest_located[2]
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"{destination_id!r} is not a container")
            if dest is item or self._contains(item, dest):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_items = dest["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_items):
                raise ValueError("index out of range")

        del source_items[source_index]
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def _locate(self, target_id):
        def search(items):
            for i, item in enumerate(items):
                if item["id"] == target_id:
                    return items, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    found = search(item["items"])
                    if found is not None:
                        return found
            return None

        return search(self._data["items"])

    @staticmethod
    def _contains(ancestor, node):
        if ancestor["kind"] not in _CONTAINER_KINDS:
            return False
        return any(descendant is node for descendant in _iter_items(ancestor["items"]))
