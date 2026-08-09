import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self._validate_workspace(self._data)

    @staticmethod
    def _validate_workspace(data):
        if not isinstance(data, dict):
            raise ValueError("workspace data must be a dictionary")
        if not isinstance(data.get("title"), str):
            raise ValueError("workspace title must be a string")
        if not isinstance(data.get("items"), list):
            raise ValueError("workspace items must be a list")

        seen = set()

        def validate_item(item):
            if not isinstance(item, dict):
                raise ValueError("items must be dictionaries")

            item_id = item.get("id")
            if not isinstance(item_id, str) or item_id in seen:
                raise ValueError("item ids must be unique strings")
            seen.add(item_id)

            kind = item.get("kind")
            if kind not in {"section", "group", "note", "reference"}:
                raise ValueError("invalid item kind")
            if not isinstance(item.get("title"), str):
                raise ValueError("item title must be a string")

            if kind in _CONTAINER_KINDS:
                children = item.get("items")
                if not isinstance(children, list):
                    raise ValueError("containers must have an items list")
                for child in children:
                    validate_item(child)
            elif kind == "note":
                if not isinstance(item.get("text"), str):
                    raise ValueError("note text must be a string")
            else:
                if not isinstance(item.get("label"), str):
                    raise ValueError("reference label must be a string")
                word_count = item.get("word_count")
                if type(word_count) is not int or word_count < 0:
                    raise ValueError("reference word_count must be nonnegative")
                unresolved = item.get("unresolved")
                if not isinstance(unresolved, list) or not all(
                    isinstance(marker, str) for marker in unresolved
                ):
                    raise ValueError("reference unresolved must contain strings")

        for item in data["items"]:
            validate_item(item)

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
                    "  " * level + item["kind"] + ": " + item["title"]
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
                return items, position, item
            if item["kind"] in _CONTAINER_KINDS:
                result = self._locate(item_id, item["items"])
                if result is not None:
                    return result
        return None

    @staticmethod
    def _contains(root, candidate):
        if root is candidate:
            return True
        if root["kind"] in _CONTAINER_KINDS:
            return any(Workspace._contains(child, candidate) for child in root["items"])
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")
        if index is not None and type(index) is not int:
            raise ValueError("index must be an integer or None")

        source = self._locate(item_id)
        if source is None:
            raise ValueError("item not found")
        source_items, source_index, item = source

        if destination_id == "workspace":
            destination_item = None
            destination_items = self._data["items"]
        else:
            destination = self._locate(destination_id)
            if destination is None:
                raise ValueError("destination not found")
            destination_item = destination[2]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination_item["items"]

        if destination_item is not None and self._contains(item, destination_item):
            raise ValueError("move would create invalid containment")

        if index is not None and not 0 <= index <= len(destination_items):
            raise ValueError("index out of range")

        moved = source_items.pop(source_index)
        if index is None:
            destination_items.append(moved)
        else:
            destination_items.insert(index, moved)

    def to_data(self):
        return copy.deepcopy(self._data)
