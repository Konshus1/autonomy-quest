import copy
import re

__all__ = ["Workspace"]

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
                for marker in item["unresolved"]:
                    match = _MARKER_RE.fullmatch(marker)
                    markers.add(match.group(1) if match else marker)
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

        add_items(self._data["items"], 1)
        return "\n".join(lines)

    def _locate(self, item_id, items=None):
        if items is None:
            items = self._data["items"]
        for position, item in enumerate(items):
            if item["id"] == item_id:
                return item, items, position
            if item["kind"] in _CONTAINER_KINDS:
                found = self._locate(item_id, item["items"])
                if found is not None:
                    return found
        return None

    def _contains(self, container, candidate):
        if container is candidate:
            return True
        if container["kind"] not in _CONTAINER_KINDS:
            return False
        return any(self._contains(child, candidate) for child in container["items"])

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        source = self._locate(item_id)
        if source is None:
            raise ValueError("item id not found")
        item, source_items, source_index = source

        destination = None
        if destination_id == "workspace":
            destination_items = self._data["items"]
        else:
            located_destination = self._locate(destination_id)
            if located_destination is None:
                raise ValueError("destination id not found")
            destination = located_destination[0]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination["items"]

        if index is None:
            insertion_index = len(destination_items)
        else:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(destination_items):
                raise ValueError("index out of range")
            insertion_index = index

        if destination is not None and self._contains(item, destination):
            raise ValueError("move would create invalid containment")

        source_items.pop(source_index)
        destination_items.insert(insertion_index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
