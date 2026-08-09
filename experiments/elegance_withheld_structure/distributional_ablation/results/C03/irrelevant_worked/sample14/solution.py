import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}
_ITEM_KINDS = _CONTAINER_KINDS | {"note", "reference"}


class Workspace:
    def __init__(self, data):
        self._validate_workspace(data)
        self._data = copy.deepcopy(data)

    @classmethod
    def _validate_workspace(cls, data):
        if not isinstance(data, dict):
            raise ValueError("workspace data must be a dictionary")
        if set(data) != {"title", "items"}:
            raise ValueError("invalid workspace fields")
        if not isinstance(data["title"], str):
            raise ValueError("workspace title must be a string")
        if not isinstance(data["items"], list):
            raise ValueError("workspace items must be a list")

        seen = set()
        for item in data["items"]:
            cls._validate_item(item, seen)

    @classmethod
    def _validate_item(cls, item, seen):
        if not isinstance(item, dict):
            raise ValueError("items must be dictionaries")

        item_id = item.get("id")
        kind = item.get("kind")
        title = item.get("title")

        if not isinstance(item_id, str):
            raise ValueError("item ids must be strings")
        if item_id in seen:
            raise ValueError("item ids must be unique")
        seen.add(item_id)

        if kind not in _ITEM_KINDS:
            raise ValueError("invalid item kind")
        if not isinstance(title, str):
            raise ValueError("item titles must be strings")

        if kind in _CONTAINER_KINDS:
            if set(item) != {"id", "kind", "title", "items"}:
                raise ValueError("invalid container fields")
            if not isinstance(item["items"], list):
                raise ValueError("container items must be a list")
            for child in item["items"]:
                cls._validate_item(child, seen)
        elif kind == "note":
            if set(item) != {"id", "kind", "title", "text"}:
                raise ValueError("invalid note fields")
            if not isinstance(item["text"], str):
                raise ValueError("note text must be a string")
        else:
            required = {"id", "kind", "title", "label", "word_count", "unresolved"}
            if set(item) != required:
                raise ValueError("invalid reference fields")
            if not isinstance(item["label"], str):
                raise ValueError("reference label must be a string")
            count = item["word_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("reference word_count must be a nonnegative integer")
            unresolved = item["unresolved"]
            if not isinstance(unresolved, list) or not all(
                isinstance(marker, str) for marker in unresolved
            ):
                raise ValueError("reference unresolved must be a list of strings")

    @staticmethod
    def _walk(items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from Workspace._walk(item["items"])

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

        def add_items(items, level):
            for item in items:
                lines.append(
                    "  " * level + "{}: {}".format(item["kind"], item["title"])
                )
                if item["kind"] in _CONTAINER_KINDS:
                    add_items(item["items"], level + 1)

        add_items(self._data["items"], 0)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")
        if index is not None and (isinstance(index, bool) or not isinstance(index, int)):
            raise ValueError("index must be an integer or None")

        source_item = None
        source_items = None
        source_index = None
        destination_items = self._data["items"] if destination_id == "workspace" else None

        def search(items):
            nonlocal source_item, source_items, source_index, destination_items
            for position, item in enumerate(items):
                if item["id"] == item_id:
                    source_item = item
                    source_items = items
                    source_index = position
                if destination_id != "workspace" and item["id"] == destination_id:
                    if item["kind"] not in _CONTAINER_KINDS:
                        raise ValueError("destination is not a container")
                    destination_items = item["items"]
                if item["kind"] in _CONTAINER_KINDS:
                    search(item["items"])

        search(self._data["items"])

        if source_item is None:
            raise ValueError("item id not found")
        if destination_items is None:
            raise ValueError("destination id not found")

        destination_length = len(destination_items)
        if index is not None and not 0 <= index <= destination_length:
            raise ValueError("index out of range")

        if destination_id != "workspace":
            subtree_ids = {item["id"] for item in self._walk([source_item])}
            if destination_id in subtree_ids:
                raise ValueError("a container cannot be moved into itself or its descendants")

        source_items.pop(source_index)
        if index is None:
            destination_items.append(source_item)
        else:
            destination_items.insert(index, source_item)

    def to_data(self):
        return copy.deepcopy(self._data)
