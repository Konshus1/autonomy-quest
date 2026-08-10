import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def total_word_count(self):
        def count(items):
            total = 0
            for item in items:
                kind = item["kind"]
                if kind == "note":
                    # Markers become spaces so words on either side stay separate
                    # while the identifier itself is never counted.
                    text = _MARKER_RE.sub(" ", item["text"])
                    total += len(_WORD_RE.findall(text))
                elif kind == "reference":
                    total += item["word_count"]
                else:
                    total += count(item["items"])
            return total

        return count(self._data["items"])

    def unresolved_markers(self):
        found = set()

        def collect(items):
            for item in items:
                kind = item["kind"]
                if kind == "note":
                    found.update(_MARKER_RE.findall(item["text"]))
                elif kind == "reference":
                    found.update(item["unresolved"])
                else:
                    collect(item["items"])

        collect(self._data["items"])
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]

        def render(items, level):
            for item in items:
                lines.append("  " * level + f"{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    render(item["items"], level + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        found = self._find(item_id, self._data["items"])
        if found is None:
            raise ValueError(f"no item with id {item_id!r}")
        parent_items, position, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_found = self._find(destination_id, self._data["items"])
            if dest_found is None:
                raise ValueError(f"no destination with id {destination_id!r}")
            destination = dest_found[2]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError(
                    f"destination {destination_id!r} is not a container"
                )
            if self._contains(item, destination_id):
                raise ValueError(
                    "cannot move an item into itself or its own subtree"
                )
            dest_items = destination["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_items):
                raise ValueError(f"index {index} out of range")

        parent_items.pop(position)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)

    @classmethod
    def _find(cls, target_id, items):
        for i, item in enumerate(items):
            if item["id"] == target_id:
                return items, i, item
            if item["kind"] in _CONTAINER_KINDS:
                result = cls._find(target_id, item["items"])
                if result is not None:
                    return result
        return None

    @classmethod
    def _contains(cls, item, target_id):
        if item["id"] == target_id:
            return True
        if item["kind"] in _CONTAINER_KINDS:
            return any(
                cls._contains(child, target_id) for child in item["items"]
            )
        return False
