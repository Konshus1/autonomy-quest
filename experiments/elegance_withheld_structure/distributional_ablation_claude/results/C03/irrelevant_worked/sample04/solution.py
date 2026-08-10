import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def _walk(self, items=None, depth=1):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item, depth
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    def total_word_count(self):
        total = 0
        for item, _ in self._walk():
            if item["kind"] == "note":
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
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
            lines.append("  " * depth + f"{item['kind']}: {item['title']}")
        return "\n".join(lines)

    def _find(self, item_id, items=None):
        if items is None:
            items = self._data["items"]
        for pos, item in enumerate(items):
            if item["id"] == item_id:
                return items, pos, item
            if item["kind"] in _CONTAINER_KINDS:
                found = self._find(item_id, item["items"])
                if found is not None:
                    return found
        if items is self._data["items"]:
            raise ValueError(f"no item with id {item_id!r}")
        return None

    def _destination_items(self, destination_id):
        if destination_id == "workspace":
            return self._data["items"]
        _, _, dest = self._find(destination_id)
        if dest["kind"] not in _CONTAINER_KINDS:
            raise ValueError(f"{destination_id!r} is not a container")
        return dest["items"]

    def _subtree_contains(self, item, target_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        return any(
            child["id"] == target_id or self._subtree_contains(child, target_id)
            for child in item["items"]
        )

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")
        source_items, pos, item = self._find(item_id)
        dest_items = self._destination_items(destination_id)
        if destination_id == item_id or self._subtree_contains(item, destination_id):
            raise ValueError("cannot move a container into itself or its descendants")
        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_items):
                raise ValueError("index out of range")
        source_items.pop(pos)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
