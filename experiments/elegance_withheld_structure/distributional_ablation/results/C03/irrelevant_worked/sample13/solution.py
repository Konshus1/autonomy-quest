from copy import deepcopy
import operator
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}
_LEAF_KINDS = {"note", "reference"}


class Workspace:
    def __init__(self, data):
        self._data = deepcopy(data)
        self._validate()

    def _validate(self):
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

                if not isinstance(item_id, str):
                    raise ValueError("item ids must be strings")
                if item_id in seen:
                    raise ValueError("item ids must be unique")
                seen.add(item_id)

                if kind not in _CONTAINER_KINDS | _LEAF_KINDS:
                    raise ValueError("unknown item kind")
                if not isinstance(title, str):
                    raise ValueError("item titles must be strings")

                if kind in _CONTAINER_KINDS:
                    if not isinstance(item.get("items"), list):
                        raise ValueError("containers must have an items list")
                    validate_items(item["items"])
                elif kind == "note":
                    if not isinstance(item.get("text"), str):
                        raise ValueError("notes must have string text")
                else:
                    if not isinstance(item.get("label"), str):
                        raise ValueError("references must have a string label")
                    count = item.get("word_count")
                    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                        raise ValueError("reference word_count must be a nonnegative integer")
                    unresolved = item.get("unresolved")
                    if not isinstance(unresolved, list) or not all(
                        isinstance(marker, str) for marker in unresolved
                    ):
                        raise ValueError("reference unresolved markers must be strings")

        validate_items(self._data["items"])

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

        def add_items(items, depth):
            for item in items:
                lines.append(
                    "  " * depth + "{}: {}".format(item["kind"], item["title"])
                )
                if item["kind"] in _CONTAINER_KINDS:
                    add_items(item["items"], depth + 1)

        add_items(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")

        source_item = None
        source_list = None

        def find_source(items):
            nonlocal source_item, source_list
            for item in items:
                if item["id"] == item_id:
                    source_item = item
                    source_list = items
                    return True
                if item["kind"] in _CONTAINER_KINDS and find_source(item["items"]):
                    return True
            return False

        find_source(self._data["items"])
        if source_item is None:
            raise ValueError("item id not found")

        if destination_id == "workspace":
            destination_list = self._data["items"]
            destination_item = None
        else:
            destination_item = next(
                (item for item in self._walk(self._data["items"])
                 if item["id"] == destination_id),
                None,
            )
            if destination_item is None:
                raise ValueError("destination id not found")
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_list = destination_item["items"]

        if destination_item is source_item:
            raise ValueError("an item cannot contain itself")
        if source_item["kind"] in _CONTAINER_KINDS and destination_item is not None:
            if any(node is destination_item for node in self._walk(source_item["items"])):
                raise ValueError("a container cannot be moved into its descendant")

        if index is None:
            insertion_index = None
        else:
            try:
                insertion_index = operator.index(index)
            except TypeError as exc:
                raise ValueError("index must be an integer") from exc
            if not 0 <= insertion_index <= len(destination_list):
                raise ValueError("index out of range")

        source_list.remove(source_item)
        if insertion_index is None:
            destination_list.append(source_item)
        else:
            destination_list.insert(insertion_index, source_item)

    def to_data(self):
        return deepcopy(self._data)
