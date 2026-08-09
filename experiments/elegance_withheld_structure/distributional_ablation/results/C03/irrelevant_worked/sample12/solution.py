import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}
_ITEM_KINDS = _CONTAINER_KINDS | {"note", "reference"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
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
                        raise ValueError("reference labels must be strings")
                    word_count = item.get("word_count")
                    if (
                        not isinstance(word_count, int)
                        or isinstance(word_count, bool)
                        or word_count < 0
                    ):
                        raise ValueError("reference word_count must be nonnegative")
                    unresolved = item.get("unresolved")
                    if not isinstance(unresolved, list) or any(
                        not isinstance(marker, str) for marker in unresolved
                    ):
                        raise ValueError("reference unresolved must contain strings")

        validate_items(self._data["items"])

    def _walk(self, items=None, depth=1):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item, items, depth
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    def total_word_count(self):
        total = 0
        for item, _, _ in self._walk():
            if item["kind"] == "note":
                text_without_markers = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text_without_markers))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item, _, _ in self._walk():
            if item["kind"] == "note":
                markers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self):
        lines = [self._data["title"]]
        for item, _, depth in self._walk():
            lines.append(
                "  " * depth + "{}: {}".format(item["kind"], item["title"])
            )
        return "\n".join(lines)

    def _find(self, item_id):
        for item, parent, _ in self._walk():
            if item["id"] == item_id:
                return item, parent
        return None

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")

        source = self._find(item_id)
        if source is None:
            raise ValueError("item id not found")
        item, source_items = source

        if destination_id == "workspace":
            destination_items = self._data["items"]
            destination = None
        else:
            found_destination = self._find(destination_id)
            if found_destination is None:
                raise ValueError("destination id not found")
            destination, _ = found_destination
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer")
            if index < 0 or index > len(destination_items):
                raise ValueError("index out of range")

        if destination is item:
            raise ValueError("an item cannot contain itself")

        if item["kind"] in _CONTAINER_KINDS and destination is not None:
            def contains(container, candidate):
                for child in container["items"]:
                    if child is candidate:
                        return True
                    if child["kind"] in _CONTAINER_KINDS and contains(child, candidate):
                        return True
                return False

            if contains(item, destination):
                raise ValueError("move would create a containment cycle")

        source_items.remove(item)
        if index is None:
            destination_items.append(item)
        else:
            destination_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
