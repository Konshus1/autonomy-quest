import copy
import re

_MARKER_RE = re.compile(r"\[\[\?([^\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def _iter_items(self, items=None):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._iter_items(item["items"])

    def total_word_count(self):
        total = 0
        for item in self._iter_items():
            if item["kind"] == "note":
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

    def outline(self):
        lines = [self._data["title"]]

        def walk(items, depth):
            for item in items:
                lines.append("  " * depth + f"{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    walk(item["items"], depth + 1)

        walk(self._data["items"], 1)
        return "\n".join(lines)

    def _find(self, item_id):
        def search(items):
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    return items, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    hit = search(item["items"])
                    if hit is not None:
                        return hit
            return None

        return search(self._data["items"])

    def _contains(self, item, target_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        for child in item["items"]:
            if child["id"] == target_id or self._contains(child, target_id):
                return True
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        found = self._find(item_id)
        if found is None:
            raise ValueError(f"no item with id {item_id!r}")
        source_list, source_index, item = found

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError(f"no item with id {destination_id!r}")
            dest_item = dest_found[2]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"destination {destination_id!r} is not a container")
            if dest_item["id"] == item_id or self._contains(item, destination_id):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_list = dest_item["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_list):
                raise ValueError(f"index {index} out of range")

        source_list.pop(source_index)
        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
