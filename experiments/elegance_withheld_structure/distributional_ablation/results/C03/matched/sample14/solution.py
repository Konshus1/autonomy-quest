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
        if set(self._data) != {"title", "items"}:
            raise ValueError("invalid workspace fields")
        if not isinstance(self._data["title"], str):
            raise ValueError("workspace title must be a string")
        if not isinstance(self._data["items"], list):
            raise ValueError("workspace items must be a list")

        seen = set()

        def validate_item(item):
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
                    raise ValueError("reference word_count must be a nonnegative integer")
                unresolved = item["unresolved"]
                if not isinstance(unresolved, list) or any(
                    not isinstance(marker, str) for marker in unresolved
                ):
                    raise ValueError("reference unresolved must contain strings")

        for item in self._data["items"]:
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

        def append_items(items, level):
            for item in items:
                lines.append(
                    f"{'  ' * level}{item['kind']}: {item['title']}"
                )
                if item["kind"] in _CONTAINER_KINDS:
                    append_items(item["items"], level + 1)

        append_items(self._data["items"], 1)
        return "\n".join(lines)

    def _locate(self, item_id):
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

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")
        if index is not None and (
            isinstance(index, bool) or not isinstance(index, int)
        ):
            raise ValueError("index must be an integer or None")

        located = self._locate(item_id)
        if located is None:
            raise ValueError("item id not found")
        item, source_items, source_index = located

        if destination_id == "workspace":
            destination_items = self._data["items"]
            destination = None
        else:
            destination_located = self._locate(destination_id)
            if destination_located is None:
                raise ValueError("destination id not found")
            destination = destination_located[0]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination["items"]

        if index is not None and not 0 <= index <= len(destination_items):
            raise ValueError("index out of range")

        if item["kind"] in _CONTAINER_KINDS and destination is not None:
            if destination is item or any(
                descendant is destination for descendant in self._walk(item["items"])
            ):
                raise ValueError("move would create a containment cycle")

        moved_item = source_items.pop(source_index)
        if index is None:
            destination_items.append(moved_item)
        else:
            destination_items.insert(index, moved_item)

    def to_data(self):
        return copy.deepcopy(self._data)
