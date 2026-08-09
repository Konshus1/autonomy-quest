import copy
import re

__all__ = ["Workspace"]

_MARKER_RE = re.compile(r"\[\[\?([^\[\]\r\n]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}
_ITEM_KINDS = _CONTAINER_KINDS | {"note", "reference"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self._validate_data()

    def _validate_data(self):
        if not isinstance(self._data, dict):
            raise ValueError("workspace data must be a dictionary")
        if not isinstance(self._data.get("title"), str):
            raise ValueError("workspace title must be a string")
        if not isinstance(self._data.get("items"), list):
            raise ValueError("workspace items must be a list")

        seen = set()

        def validate_item(item):
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
                raise ValueError("unknown item kind")
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
                    raise ValueError("reference word_count must be a nonnegative integer")
                unresolved = item.get("unresolved")
                if not isinstance(unresolved, list) or any(
                    not isinstance(marker, str) for marker in unresolved
                ):
                    raise ValueError("reference unresolved markers must be strings")

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

        def add_items(items, depth):
            for item in items:
                lines.append(
                    f"{'  ' * depth}{item['kind']}: {item['title']}"
                )
                if item["kind"] in _CONTAINER_KINDS:
                    add_items(item["items"], depth + 1)

        add_items(self._data["items"], 1)
        return "\n".join(lines)

    def _locate(self, item_id):
        def search(items):
            for position, item in enumerate(items):
                if item["id"] == item_id:
                    return items, position, item
                if item["kind"] in _CONTAINER_KINDS:
                    found = search(item["items"])
                    if found is not None:
                        return found
            return None

        return search(self._data["items"])

    @staticmethod
    def _contains(container, candidate):
        if container is candidate:
            return True
        if container["kind"] not in _CONTAINER_KINDS:
            return False
        return any(
            Workspace._contains(child, candidate)
            for child in container["items"]
        )

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")

        source = self._locate(item_id)
        if source is None:
            raise ValueError("item id not found")
        source_items, source_index, item = source

        if destination_id == "workspace":
            destination = None
            destination_items = self._data["items"]
        else:
            located_destination = self._locate(destination_id)
            if located_destination is None:
                raise ValueError("destination id not found")
            destination = located_destination[2]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(destination_items):
                raise ValueError("index out of range")

        if destination is not None and self._contains(item, destination):
            raise ValueError("move would create invalid containment")

        source_items.pop(source_index)
        if index is None:
            destination_items.append(item)
        else:
            destination_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
