import copy
import operator
import re


_MARKER_RE = re.compile(r"\[\[\?([^\]]+)\]\]")
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
                text_without_markers = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text_without_markers))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item, _ in self._walk():
            if item["kind"] == "note":
                markers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self):
        lines = [self._data["title"]]
        for item, depth in self._walk():
            lines.append(
                "  " * depth + "{}: {}".format(item["kind"], item["title"])
            )
        return "\n".join(lines)

    def _find_item(self, item_id, items=None, parent_items=None):
        if items is None:
            items = self._data["items"]

        for position, item in enumerate(items):
            if item["id"] == item_id:
                return item, items, position
            if item["kind"] in _CONTAINER_KINDS:
                found = self._find_item(item_id, item["items"], items)
                if found is not None:
                    return found
        return None

    def _contains(self, container, possible_descendant):
        if container is possible_descendant:
            return True
        if container["kind"] not in _CONTAINER_KINDS:
            return False
        for child in container["items"]:
            if self._contains(child, possible_descendant):
                return True
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        source = self._find_item(item_id)
        if source is None:
            raise ValueError("item id not found")
        item, source_items, source_index = source

        if destination_id == "workspace":
            destination = None
            destination_items = self._data["items"]
        else:
            destination_result = self._find_item(destination_id)
            if destination_result is None:
                raise ValueError("destination id not found")
            destination = destination_result[0]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination["items"]

        if destination is not None and self._contains(item, destination):
            raise ValueError("move would create invalid containment")

        if index is None:
            insertion_index = len(destination_items)
        else:
            try:
                insertion_index = operator.index(index)
            except TypeError as exc:
                raise ValueError("index must be an integer") from exc
            if insertion_index < 0 or insertion_index > len(destination_items):
                raise ValueError("index out of range")

        source_items.pop(source_index)
        destination_items.insert(insertion_index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
