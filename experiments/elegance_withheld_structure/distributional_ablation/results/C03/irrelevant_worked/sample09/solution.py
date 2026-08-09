import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    @staticmethod
    def _walk(items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from Workspace._walk(item["items"])

    @staticmethod
    def _find(items, item_id):
        for position, item in enumerate(items):
            if item["id"] == item_id:
                return items, position, item
            if item["kind"] in _CONTAINER_KINDS:
                found = Workspace._find(item["items"], item_id)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _contains(container, candidate):
        if container is candidate:
            return True
        if container["kind"] not in _CONTAINER_KINDS:
            return False
        return any(Workspace._contains(child, candidate)
                   for child in container["items"])

    def total_word_count(self):
        total = 0
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                text_without_markers = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text_without_markers))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item in self._walk(self._data["items"]):
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
                    "  " * level + item["kind"] + ": " + item["title"]
                )
                if item["kind"] in _CONTAINER_KINDS:
                    add_items(item["items"], level + 1)

        add_items(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        candidate = copy.deepcopy(self._data)
        source = self._find(candidate["items"], item_id)
        if source is None:
            raise ValueError("item id not found")
        source_items, source_index, item = source

        if destination_id == "workspace":
            destination = None
            destination_items = candidate["items"]
        else:
            found_destination = self._find(candidate["items"], destination_id)
            if found_destination is None:
                raise ValueError("destination id not found")
            destination = found_destination[2]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(destination_items):
                raise ValueError("index out of range")

        if destination is not None and self._contains(item, destination):
            raise ValueError("move would create invalid containment")

        source_items.pop(source_index)
        if index is None:
            destination_items.append(item)
        else:
            destination_items.insert(index, item)

        self._data = candidate

    def to_data(self):
        return copy.deepcopy(self._data)
