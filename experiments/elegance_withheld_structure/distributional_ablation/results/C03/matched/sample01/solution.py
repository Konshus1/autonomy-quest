import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    @staticmethod
    def _children(item):
        if item.get("kind") in _CONTAINER_KINDS:
            return item["items"]
        return ()

    @classmethod
    def _walk(cls, items):
        for item in items:
            yield item
            yield from cls._walk(cls._children(item))

    def total_word_count(self):
        total = 0
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                text_without_markers = _MARKER_RE.sub("", item["text"])
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

        def render(items, depth):
            for item in items:
                lines.append(
                    "  " * depth + item["kind"] + ": " + item["title"]
                )
                render(self._children(item), depth + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    @classmethod
    def _find(cls, items, wanted_id):
        for position, item in enumerate(items):
            if item["id"] == wanted_id:
                return items, position, item
            found = cls._find(cls._children(item), wanted_id)
            if found is not None:
                return found
        return None

    @classmethod
    def _contains_id(cls, item, wanted_id):
        return any(
            descendant["id"] == wanted_id
            for descendant in cls._walk(cls._children(item))
        )

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        candidate = copy.deepcopy(self._data)
        source = self._find(candidate["items"], item_id)
        if source is None:
            raise ValueError("item id not found")
        source_items, source_index, item = source

        if destination_id == "workspace":
            destination_items = candidate["items"]
        else:
            destination = self._find(candidate["items"], destination_id)
            if destination is None:
                raise ValueError("destination id not found")
            destination_item = destination[2]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            if destination_item is item or self._contains_id(item, destination_id):
                raise ValueError("move would create a containment cycle")
            destination_items = destination_item["items"]

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

        self._data = candidate

    def to_data(self):
        return copy.deepcopy(self._data)
