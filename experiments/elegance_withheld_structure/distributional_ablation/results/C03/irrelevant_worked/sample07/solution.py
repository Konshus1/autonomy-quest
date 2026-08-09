import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}
_ITEM_KINDS = _CONTAINER_KINDS | {"note", "reference"}


class Workspace:
    def __init__(self, data):
        try:
            copied = copy.deepcopy(data)
        except Exception as exc:
            raise ValueError("workspace data cannot be copied") from exc

        self._validate_workspace(copied)
        self._data = copied

    @staticmethod
    def _validate_workspace(data):
        if not isinstance(data, dict):
            raise ValueError("workspace data must be a dictionary")
        if not isinstance(data.get("title"), str):
            raise ValueError("workspace title must be a string")
        if not isinstance(data.get("items"), list):
            raise ValueError("workspace items must be a list")

        seen_ids = set()

        def validate_item(item):
            if not isinstance(item, dict):
                raise ValueError("each item must be a dictionary")

            item_id = item.get("id")
            if not isinstance(item_id, str):
                raise ValueError("item ids must be strings")
            if item_id in seen_ids:
                raise ValueError("item ids must be unique")
            seen_ids.add(item_id)

            kind = item.get("kind")
            if kind not in _ITEM_KINDS:
                raise ValueError("invalid item kind")
            if not isinstance(item.get("title"), str):
                raise ValueError("item titles must be strings")

            if kind in _CONTAINER_KINDS:
                children = item.get("items")
                if not isinstance(children, list):
                    raise ValueError("containers must have an items list")
                for child in children:
                    validate_item(child)
            elif kind == "note":
                if not isinstance(item.get("text"), str):
                    raise ValueError("notes must have string text")
            else:
                if not isinstance(item.get("label"), str):
                    raise ValueError("references must have a string label")
                word_count = item.get("word_count")
                if (
                    not isinstance(word_count, int)
                    or isinstance(word_count, bool)
                    or word_count < 0
                ):
                    raise ValueError("reference word_count must be a nonnegative integer")
                unresolved = item.get("unresolved")
                if not isinstance(unresolved, list) or any(
                    not isinstance(marker, str) for marker in unresolved
                ):
                    raise ValueError("reference unresolved must be a list of strings")

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
                    "  " * level + "{}: {}".format(item["kind"], item["title"])
                )
                if item["kind"] in _CONTAINER_KINDS:
                    add_items(item["items"], level + 1)

        add_items(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")
        if index is not None and (
            not isinstance(index, int) or isinstance(index, bool)
        ):
            raise ValueError("index must be an integer or None")

        source_item = None
        source_list = None
        destination_item = None

        def locate(items):
            nonlocal source_item, source_list, destination_item
            for item in items:
                if item["id"] == item_id:
                    source_item = item
                    source_list = items
                if item["id"] == destination_id:
                    destination_item = item
                if item["kind"] in _CONTAINER_KINDS:
                    locate(item["items"])

        locate(self._data["items"])

        if source_item is None:
            raise ValueError("item id does not exist")

        if destination_id == "workspace":
            destination_list = self._data["items"]
        else:
            if destination_item is None:
                raise ValueError("destination id does not exist")
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_list = destination_item["items"]

        destination_length = len(destination_list)
        if index is not None and not 0 <= index <= destination_length:
            raise ValueError("index is outside the destination")

        if source_item["kind"] in _CONTAINER_KINDS:
            if destination_item is source_item:
                raise ValueError("a container cannot contain itself")

            def contains(container, candidate):
                for child in container["items"]:
                    if child is candidate:
                        return True
                    if child["kind"] in _CONTAINER_KINDS and contains(child, candidate):
                        return True
                return False

            if destination_item is not None and contains(source_item, destination_item):
                raise ValueError("a container cannot move into its descendant")

        source_list.remove(source_item)
        if index is None:
            destination_list.append(source_item)
        else:
            destination_list.insert(index, source_item)

    def to_data(self):
        return copy.deepcopy(self._data)
