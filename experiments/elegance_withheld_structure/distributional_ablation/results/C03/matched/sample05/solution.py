import copy
import operator
import re

__all__ = ["Workspace"]

_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}
_LEAF_KINDS = {"note", "reference"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self._validate(self._data)

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict):
            raise ValueError("workspace data must be a dictionary")
        if not isinstance(data.get("title"), str):
            raise ValueError("workspace title must be a string")
        if not isinstance(data.get("items"), list):
            raise ValueError("workspace items must be a list")

        identifiers = set()
        active_objects = set()

        def validate_item(item):
            if not isinstance(item, dict):
                raise ValueError("each item must be a dictionary")

            object_id = id(item)
            if object_id in active_objects:
                raise ValueError("cyclic input data")
            active_objects.add(object_id)

            try:
                item_id = item.get("id")
                kind = item.get("kind")

                if not isinstance(item_id, str):
                    raise ValueError("item ids must be strings")
                if item_id in identifiers:
                    raise ValueError("item ids must be unique")
                identifiers.add(item_id)

                if kind not in _CONTAINER_KINDS | _LEAF_KINDS:
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
                    if (
                        not isinstance(word_count, int)
                        or isinstance(word_count, bool)
                        or word_count < 0
                    ):
                        raise ValueError(
                            "reference word_count must be a nonnegative integer"
                        )
                    unresolved = item.get("unresolved")
                    if not isinstance(unresolved, list) or any(
                        not isinstance(marker, str) for marker in unresolved
                    ):
                        raise ValueError(
                            "reference unresolved must be a list of strings"
                        )
            finally:
                active_objects.remove(object_id)

        for item in data["items"]:
            validate_item(item)

    @staticmethod
    def _iter_items(items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from Workspace._iter_items(item["items"])

    def total_word_count(self):
        total = 0
        for item in self._iter_items(self._data["items"]):
            if item["kind"] == "note":
                text_without_markers = _MARKER_RE.sub("", item["text"])
                total += len(_WORD_RE.findall(text_without_markers))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item in self._iter_items(self._data["items"]):
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

    @staticmethod
    def _locations(data):
        locations = {}

        def visit(items):
            for position, item in enumerate(items):
                locations[item["id"]] = (item, items, position)
                if item["kind"] in _CONTAINER_KINDS:
                    visit(item["items"])

        visit(data["items"])
        return locations

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        if index is not None:
            try:
                insertion_index = operator.index(index)
            except TypeError as exc:
                raise ValueError("index must be an integer or None") from exc
        else:
            insertion_index = None

        candidate = copy.deepcopy(self._data)
        locations = self._locations(candidate)

        if item_id not in locations:
            raise ValueError("missing item id")

        moving_item, source_items, source_position = locations[item_id]

        if destination_id == "workspace":
            destination_items = candidate["items"]
        else:
            destination_location = locations.get(destination_id)
            if destination_location is None:
                raise ValueError("missing destination id")
            destination_item = destination_location[0]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination_item["items"]

        if moving_item["kind"] in _CONTAINER_KINDS:
            descendant_ids = {
                descendant["id"]
                for descendant in self._iter_items(moving_item["items"])
            }
            if destination_id == item_id or destination_id in descendant_ids:
                raise ValueError("move would create a containment cycle")

        destination_length = len(destination_items)
        if insertion_index is not None and not (
            0 <= insertion_index <= destination_length
        ):
            raise ValueError("index is out of range")

        item = source_items.pop(source_position)
        if insertion_index is None:
            destination_items.append(item)
        else:
            destination_items.insert(insertion_index, item)

        self._data = candidate

    def to_data(self):
        return copy.deepcopy(self._data)
