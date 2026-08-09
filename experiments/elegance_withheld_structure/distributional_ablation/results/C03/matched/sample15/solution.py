import copy
import operator
import re

__all__ = ["Workspace"]


class Workspace:
    _MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
    _WORD_RE = re.compile(r"\b\w+\b")
    _CONTAINER_KINDS = {"section", "group"}

    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def _walk(self, items=None):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item
            if item["kind"] in self._CONTAINER_KINDS:
                yield from self._walk(item["items"])

    def total_word_count(self):
        total = 0
        for item in self._walk():
            if item["kind"] == "note":
                # Replacing markers with whitespace prevents adjacent words from
                # being merged while excluding both marker syntax and identifier.
                visible_text = self._MARKER_RE.sub(" ", item["text"])
                total += len(self._WORD_RE.findall(visible_text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item in self._walk():
            if item["kind"] == "note":
                markers.update(self._MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self):
        lines = [self._data["title"]]

        def render(items, level):
            for item in items:
                lines.append(
                    "  " * level + item["kind"] + ": " + item["title"]
                )
                if item["kind"] in self._CONTAINER_KINDS:
                    render(item["items"], level + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def _locate(self, item_id, items=None):
        if items is None:
            items = self._data["items"]
        for position, item in enumerate(items):
            if item["id"] == item_id:
                return item, items, position
            if item["kind"] in self._CONTAINER_KINDS:
                found = self._locate(item_id, item["items"])
                if found is not None:
                    return found
        return None

    def _contains_object(self, container, target):
        if container is target:
            return True
        if container["kind"] not in self._CONTAINER_KINDS:
            return False
        return any(
            self._contains_object(child, target)
            for child in container["items"]
        )

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        source = self._locate(item_id)
        if source is None:
            raise ValueError("item id not found")
        item, source_items, source_position = source

        destination_item = None
        if destination_id == "workspace":
            destination_items = self._data["items"]
        else:
            destination = self._locate(destination_id)
            if destination is None:
                raise ValueError("destination id not found")
            destination_item = destination[0]
            if destination_item["kind"] not in self._CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination_item["items"]

        if index is None:
            insertion_index = len(destination_items)
        else:
            try:
                insertion_index = operator.index(index)
            except TypeError as exc:
                raise ValueError("index must be an integer") from exc
            if not 0 <= insertion_index <= len(destination_items):
                raise ValueError("index out of range")

        if (
            destination_item is not None
            and item["kind"] in self._CONTAINER_KINDS
            and self._contains_object(item, destination_item)
        ):
            raise ValueError("move would create a containment cycle")

        moved_item = source_items.pop(source_position)
        destination_items.insert(insertion_index, moved_item)

    def to_data(self):
        return copy.deepcopy(self._data)
