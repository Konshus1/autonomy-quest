import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def _walk(self, items=None):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"])

    def total_word_count(self):
        total = 0
        for item in self._walk():
            if item["kind"] == "note":
                text_without_markers = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text_without_markers))
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

        def add_items(items, level):
            for item in items:
                lines.append(
                    "  " * level + f'{item["kind"]}: {item["title"]}'
                )
                if item["kind"] in _CONTAINER_KINDS:
                    add_items(item["items"], level + 1)

        add_items(self._data["items"], 0)
        return "\n".join(lines)

    def _find_item(self, item_id, items=None):
        if items is None:
            items = self._data["items"]
        for position, item in enumerate(items):
            if item["id"] == item_id:
                return item, items, position
            if item["kind"] in _CONTAINER_KINDS:
                found = self._find_item(item_id, item["items"])
                if found is not None:
                    return found
        return None

    def _contains_identity(self, root, candidate):
        if root is candidate:
            return True
        if root["kind"] in _CONTAINER_KINDS:
            return any(
                self._contains_identity(child, candidate)
                for child in root["items"]
            )
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        source = self._find_item(item_id)
        if source is None:
            raise ValueError("item id not found")
        item, source_items, source_index = source

        if destination_id == "workspace":
            destination_item = None
            destination_items = self._data["items"]
        else:
            destination = self._find_item(destination_id)
            if destination is None:
                raise ValueError("destination id not found")
            destination_item = destination[0]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination_item["items"]

        if index is not None:
            if not isinstance(index, int) or not 0 <= index <= len(destination_items):
                raise ValueError("index is out of range")

        if (
            item["kind"] in _CONTAINER_KINDS
            and destination_item is not None
            and self._contains_identity(item, destination_item)
        ):
            raise ValueError("move would create a containment cycle")

        moved_item = source_items.pop(source_index)
        if index is None:
            destination_items.append(moved_item)
        else:
            destination_items.insert(index, moved_item)

    def to_data(self):
        return copy.deepcopy(self._data)
