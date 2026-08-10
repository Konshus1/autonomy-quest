import copy
import re

_MARKER_RE = re.compile(r"\[\[\?([^\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def to_data(self):
        return copy.deepcopy(self._data)

    def _walk(self, items, level=1):
        for item in items:
            yield item, level
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], level + 1)

    def total_word_count(self):
        total = 0
        for item, _ in self._walk(self._data["items"]):
            if item["kind"] == "note":
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
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
        for item, level in self._walk(self._data["items"]):
            lines.append("  " * level + f"{item['kind']}: {item['title']}")
        return "\n".join(lines)

    def _find(self, item_id):
        stack = [self._data["items"]]
        while stack:
            items = stack.pop()
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    return items, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    stack.append(item["items"])
        return None

    @staticmethod
    def _subtree_contains(container, target):
        if container["kind"] not in _CONTAINER_KINDS:
            return False
        stack = list(container["items"])
        while stack:
            node = stack.pop()
            if node is target:
                return True
            if node["kind"] in _CONTAINER_KINDS:
                stack.extend(node["items"])
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        found = self._find(item_id)
        if found is None:
            raise ValueError(f"no item with id {item_id!r}")
        source_items, source_index, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError(f"no destination with id {destination_id!r}")
            _, _, dest = dest_found
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError(
                    f"destination {destination_id!r} is not a container"
                )
            if dest is item or self._subtree_contains(item, dest):
                raise ValueError(
                    "cannot move an item into itself or its descendants"
                )
            dest_items = dest["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_items):
                raise ValueError(f"index {index} out of range")

        source_items.pop(source_index)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)
