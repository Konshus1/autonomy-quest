import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]\r\n]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def total_word_count(self):
        total = 0
        for item in self._walk_items(self._data["items"]):
            if item["kind"] == "note":
                text_without_markers = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text_without_markers))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item in self._walk_items(self._data["items"]):
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
                    "  " * level + "{}: {}".format(item["kind"], item["title"])
                )
                if item["kind"] in _CONTAINER_KINDS:
                    add_items(item["items"], level + 1)

        add_items(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")

        source = self._find_item(item_id)
        if source is None:
            raise ValueError("item not found")
        item, source_items, source_index = source

        if destination_id == "workspace":
            destination_items = self._data["items"]
        else:
            destination = self._find_item(destination_id)
            if destination is None:
                raise ValueError("destination not found")
            destination_item = destination[0]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination_item["items"]

        if item["kind"] in _CONTAINER_KINDS:
            if destination_id == item_id or self._contains_id(item, destination_id):
                raise ValueError("move would create invalid containment")

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(destination_items):
                raise ValueError("index out of range")

        source_items.pop(source_index)
        if index is None:
            destination_items.append(item)
        else:
            destination_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)

    @staticmethod
    def _walk_items(items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from Workspace._walk_items(item["items"])

    def _find_item(self, item_id):
        def search(items):
            for position, item in enumerate(items):
                if item["id"] == item_id:
                    return item, items, position
                if item["kind"] in _CONTAINER_KINDS:
                    found = search(item["items"])
                    if found is not None:
                        return found
            return None

        return search(self._data["items"])

    @staticmethod
    def _contains_id(container, item_id):
        for descendant in Workspace._walk_items(container["items"]):
            if descendant["id"] == item_id:
                return True
        return False
