import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}
_ITEM_KINDS = _CONTAINER_KINDS | {"note", "reference"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self._validate_data(self._data)

    @staticmethod
    def _validate_data(data):
        if not isinstance(data, dict):
            raise ValueError("workspace data must be a dictionary")
        if not isinstance(data.get("title"), str):
            raise ValueError("workspace title must be a string")
        if not isinstance(data.get("items"), list):
            raise ValueError("workspace items must be a list")

        seen = set()

        def validate_items(items):
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("each item must be a dictionary")

                item_id = item.get("id")
                if not isinstance(item_id, str):
                    raise ValueError("item ids must be strings")
                if item_id in seen:
                    raise ValueError("item ids must be unique")
                seen.add(item_id)

                kind = item.get("kind")
                if kind not in _ITEM_KINDS:
                    raise ValueError("invalid item kind")
                if not isinstance(item.get("title"), str):
                    raise ValueError("item titles must be strings")

                if kind in _CONTAINER_KINDS:
                    if not isinstance(item.get("items"), list):
                        raise ValueError("containers must have an items list")
                    validate_items(item["items"])
                elif kind == "note":
                    if not isinstance(item.get("text"), str):
                        raise ValueError("note text must be a string")
                else:
                    if not isinstance(item.get("label"), str):
                        raise ValueError("reference label must be a string")
                    count = item.get("word_count")
                    if (not isinstance(count, int) or isinstance(count, bool)
                            or count < 0):
                        raise ValueError(
                            "reference word_count must be a nonnegative integer"
                        )
                    unresolved = item.get("unresolved")
                    if (not isinstance(unresolved, list)
                            or any(not isinstance(marker, str)
                                   for marker in unresolved)):
                        raise ValueError(
                            "reference unresolved must be a list of strings"
                        )

        validate_items(data["items"])

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
                return item, items, position
            if item["kind"] in _CONTAINER_KINDS:
                found = Workspace._find(item["items"], item_id)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _contains_id(item, item_id):
        if item["id"] == item_id:
            return True
        if item["kind"] in _CONTAINER_KINDS:
            return any(
                Workspace._contains_id(child, item_id)
                for child in item["items"]
            )
        return False

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

        def add_items(items, depth):
            for item in items:
                lines.append(
                    "  " * depth + item["kind"] + ": " + item["title"]
                )
                if item["kind"] in _CONTAINER_KINDS:
                    add_items(item["items"], depth + 1)

        add_items(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")
        if index is not None and (
            not isinstance(index, int) or isinstance(index, bool)
        ):
            raise ValueError("index must be an integer or None")

        candidate = copy.deepcopy(self._data)
        source = self._find(candidate["items"], item_id)
        if source is None:
            raise ValueError("item id not found")
        item, source_items, source_index = source

        if destination_id == "workspace":
            destination_items = candidate["items"]
        else:
            destination = self._find(candidate["items"], destination_id)
            if destination is None:
                raise ValueError("destination id not found")
            destination_item = destination[0]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            if (item["kind"] in _CONTAINER_KINDS
                    and self._contains_id(item, destination_id)):
                raise ValueError("cannot move a container into itself or a descendant")
            destination_items = destination_item["items"]

        if index is not None and not 0 <= index <= len(destination_items):
            raise ValueError("index out of range")

        source_items.pop(source_index)
        if index is None:
            destination_items.append(item)
        else:
            destination_items.insert(index, item)

        self._data = candidate

    def to_data(self):
        return copy.deepcopy(self._data)
