import copy
import re


_WORD_RE = re.compile(r"\b\w+\b")
_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self._validate_workspace()

    def _validate_workspace(self):
        if not isinstance(self._data, dict):
            raise ValueError("workspace data must be a dictionary")
        if not isinstance(self._data.get("title"), str):
            raise ValueError("workspace title must be a string")
        if not isinstance(self._data.get("items"), list):
            raise ValueError("workspace items must be a list")

        seen = set()

        def validate_items(items):
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("each item must be a dictionary")

                item_id = item.get("id")
                kind = item.get("kind")
                title = item.get("title")

                if not isinstance(item_id, str) or item_id in seen:
                    raise ValueError("item ids must be unique strings")
                if not isinstance(kind, str):
                    raise ValueError("item kind must be a string")
                if not isinstance(title, str):
                    raise ValueError("item title must be a string")
                seen.add(item_id)

                if kind in _CONTAINER_KINDS:
                    if not isinstance(item.get("items"), list):
                        raise ValueError("containers must have an items list")
                    validate_items(item["items"])
                elif kind == "note":
                    if not isinstance(item.get("text"), str):
                        raise ValueError("notes must have text")
                elif kind == "reference":
                    if not isinstance(item.get("label"), str):
                        raise ValueError("references must have a label")
                    word_count = item.get("word_count")
                    if (not isinstance(word_count, int) or
                            isinstance(word_count, bool) or word_count < 0):
                        raise ValueError("reference word_count must be nonnegative")
                    unresolved = item.get("unresolved")
                    if (not isinstance(unresolved, list) or
                            any(not isinstance(marker, str)
                                for marker in unresolved)):
                        raise ValueError("reference unresolved must contain strings")
                else:
                    raise ValueError("unknown item kind")

        validate_items(self._data["items"])

    def _walk(self, items=None, depth=1):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item, depth
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    def _find(self, item_id):
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
    def _contains_id(item, sought_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        for child in item["items"]:
            if child["id"] == sought_id:
                return True
            if Workspace._contains_id(child, sought_id):
                return True
        return False

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
            lines.append("  " * depth + item["kind"] + ": " + item["title"])
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")

        source = self._find(item_id)
        if source is None:
            raise ValueError("item id not found")
        item, source_items, source_index = source

        if destination_id == "workspace":
            destination_items = self._data["items"]
        else:
            destination = self._find(destination_id)
            if destination is None:
                raise ValueError("destination id not found")
            destination_item = destination[0]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            if destination_item is item or self._contains_id(item, destination_id):
                raise ValueError("move would create a containment cycle")
            destination_items = destination_item["items"]

        if index is not None:
            if (not isinstance(index, int) or isinstance(index, bool) or
                    index < 0 or index > len(destination_items)):
                raise ValueError("index is out of range")

        moved_item = source_items.pop(source_index)
        if index is None:
            destination_items.append(moved_item)
        else:
            destination_items.insert(index, moved_item)

    def to_data(self):
        return copy.deepcopy(self._data)
