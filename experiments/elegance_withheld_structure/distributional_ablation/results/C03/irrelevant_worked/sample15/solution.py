from copy import deepcopy
import operator
import re

__all__ = ["Workspace"]


class Workspace:
    _CONTAINER_KINDS = {"section", "group"}
    _ITEM_KINDS = _CONTAINER_KINDS | {"note", "reference"}
    _MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
    _WORD_RE = re.compile(r"\b\w+\b")

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

        seen_ids = set()
        active_objects = set()

        def validate_items(items):
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("each item must be a dictionary")

                object_id = id(item)
                if object_id in active_objects:
                    raise ValueError("cyclic input structure")
                active_objects.add(object_id)

                item_id = item.get("id")
                kind = item.get("kind")
                title = item.get("title")

                if not isinstance(item_id, str):
                    raise ValueError("item ids must be strings")
                if item_id in seen_ids:
                    raise ValueError("item ids must be unique")
                seen_ids.add(item_id)

                if kind not in self._ITEM_KINDS:
                    raise ValueError("invalid item kind")
                if not isinstance(title, str):
                    raise ValueError("item titles must be strings")

                if kind in self._CONTAINER_KINDS:
                    if not isinstance(item.get("items"), list):
                        raise ValueError("containers must have an items list")
                    validate_items(item["items"])
                elif kind == "note":
                    if not isinstance(item.get("text"), str):
                        raise ValueError("note text must be a string")
                else:
                    if not isinstance(item.get("label"), str):
                        raise ValueError("reference label must be a string")
                    word_count = item.get("word_count")
                    if type(word_count) is not int or word_count < 0:
                        raise ValueError("reference word_count must be a nonnegative integer")
                    unresolved = item.get("unresolved")
                    if not isinstance(unresolved, list) or not all(
                        isinstance(marker, str) for marker in unresolved
                    ):
                        raise ValueError("reference unresolved must be a list of strings")

                active_objects.remove(object_id)

        validate_items(self._data["items"])

    def _walk(self, items=None):
        if items is None:
            items = self._data["items"]
        for index, item in enumerate(items):
            yield item, items, index
            if item["kind"] in self._CONTAINER_KINDS:
                yield from self._walk(item["items"])

    def total_word_count(self):
        total = 0
        for item, _, _ in self._walk():
            if item["kind"] == "note":
                text_without_markers = self._MARKER_RE.sub(" ", item["text"])
                total += len(self._WORD_RE.findall(text_without_markers))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item, _, _ in self._walk():
            if item["kind"] == "note":
                markers.update(self._MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self):
        lines = [self._data["title"]]

        def render(items, depth):
            for item in items:
                lines.append(
                    f"{'  ' * depth}{item['kind']}: {item['title']}"
                )
                if item["kind"] in self._CONTAINER_KINDS:
                    render(item["items"], depth + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        records = list(self._walk())
        source = next(
            (record for record in records if record[0]["id"] == item_id),
            None,
        )
        if source is None:
            raise ValueError("item id not found")

        if destination_id == "workspace":
            destination_items = self._data["items"]
            destination_item = None
        else:
            destination_record = next(
                (
                    record
                    for record in records
                    if record[0]["id"] == destination_id
                ),
                None,
            )
            if destination_record is None:
                raise ValueError("destination id not found")
            destination_item = destination_record[0]
            if destination_item["kind"] not in self._CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination_item["items"]

        try:
            insertion_index = (
                len(destination_items) if index is None else operator.index(index)
            )
        except TypeError as exc:
            raise ValueError("index must be an integer or None") from exc

        if not 0 <= insertion_index <= len(destination_items):
            raise ValueError("index is out of range")

        moved_item, source_items, source_index = source

        if destination_item is not None:
            contained_ids = {
                descendant[0]["id"]
                for descendant in self._walk(
                    moved_item["items"]
                    if moved_item["kind"] in self._CONTAINER_KINDS
                    else []
                )
            }
            if destination_id == item_id or destination_id in contained_ids:
                raise ValueError("a container cannot be moved into itself or a descendant")

        source_items.pop(source_index)
        destination_items.insert(insertion_index, moved_item)

    def to_data(self):
        return deepcopy(self._data)
