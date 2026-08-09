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
        if not isinstance(data["title"], str) or not isinstance(data["items"], list):
            raise ValueError("invalid workspace")

        seen = set()

        def validate_item(item):
            if not isinstance(item, dict):
                raise ValueError("items must be dictionaries")

            item_id = item.get("id")
            kind = item.get("kind")
            title = item.get("title")
            if not isinstance(item_id, str) or item_id in seen:
                raise ValueError("item ids must be unique strings")
            if kind not in _ITEM_KINDS or not isinstance(title, str):
                raise ValueError("invalid item")
            seen.add(item_id)

            if kind in _CONTAINER_KINDS:
                if set(item) != {"id", "kind", "title", "items"}:
                    raise ValueError("invalid container fields")
                if not isinstance(item["items"], list):
                    raise ValueError("container items must be a list")
                for child in item["items"]:
                    validate_item(child)
            elif kind == "note":
                if set(item) != {"id", "kind", "title", "text"}:
                    raise ValueError("invalid note fields")
                if not isinstance(item["text"], str):
                    raise ValueError("note text must be a string")
            else:
                if set(item) != {
                    "id", "kind", "title", "label", "word_count", "unresolved"
                }:
                    raise ValueError("invalid reference fields")
                if not isinstance(item["label"], str):
                    raise ValueError("reference label must be a string")
                count = item["word_count"]
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ValueError("invalid reference word count")
                unresolved = item["unresolved"]
                if not isinstance(unresolved, list) or any(
                    not isinstance(marker, str) for marker in unresolved
                ):
                    raise ValueError("invalid unresolved markers")

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
                text_without_markers = _MARKER_RE.sub("", item["text"])
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

        def add_lines(items, level):
            for item in items:
                lines.append(
                    "  " * level + "{}: {}".format(item["kind"], item["title"])
                )
                if item["kind"] in _CONTAINER_KINDS:
                    add_lines(item["items"], level + 1)

        add_lines(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")

        source_item = None
        source_list = None
        destination = None

        def search(items):
            nonlocal source_item, source_list, destination
            for item in items:
                if item["id"] == item_id:
                    source_item = item
                    source_list = items
                if item["id"] == destination_id:
                    destination = item
                if item["kind"] in _CONTAINER_KINDS:
                    search(item["items"])

        search(self._data["items"])

        if source_item is None:
            raise ValueError("item not found")

        if destination_id == "workspace":
            destination_list = self._data["items"]
        else:
            if destination is None:
                raise ValueError("destination not found")
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_list = destination["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(destination_list):
                raise ValueError("index out of range")

        if source_item["kind"] in _CONTAINER_KINDS:
            if destination is source_item:
                raise ValueError("cannot move a container into itself")
            if destination is not None:
                descendants = {
                    item["id"] for item in self._walk(source_item["items"])
                }
                if destination["id"] in descendants:
                    raise ValueError("cannot move a container into its descendant")

        old_position = source_list.index(source_item)
        source_list.pop(old_position)

        if index is None:
            destination_list.append(source_item)
        else:
            adjusted_index = index
            if source_list is destination_list and old_position < index:
                adjusted_index -= 1
            destination_list.insert(adjusted_index, source_item)

    def to_data(self):
        return copy.deepcopy(self._data)
