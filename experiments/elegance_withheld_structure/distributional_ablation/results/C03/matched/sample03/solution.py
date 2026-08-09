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

        def visit(items):
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("items must be dictionaries")

                item_id = item.get("id")
                kind = item.get("kind")
                title = item.get("title")

                if not isinstance(item_id, str) or item_id in seen:
                    raise ValueError("item ids must be unique strings")
                if kind not in _ITEM_KINDS:
                    raise ValueError("invalid item kind")
                if not isinstance(title, str):
                    raise ValueError("item title must be a string")
                seen.add(item_id)

                if kind in _CONTAINER_KINDS:
                    if not isinstance(item.get("items"), list):
                        raise ValueError("containers must have an items list")
                    visit(item["items"])
                elif kind == "note":
                    if not isinstance(item.get("text"), str):
                        raise ValueError("note text must be a string")
                else:
                    if not isinstance(item.get("label"), str):
                        raise ValueError("reference label must be a string")
                    count = item.get("word_count")
                    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                        raise ValueError("reference word_count must be a nonnegative integer")
                    unresolved = item.get("unresolved")
                    if not isinstance(unresolved, list) or not all(
                        isinstance(marker, str) for marker in unresolved
                    ):
                        raise ValueError("reference unresolved must be a list of strings")

        visit(self._data["items"])

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

        def render(items, level):
            for item in items:
                lines.append(
                    "  " * level + item["kind"] + ": " + item["title"]
                )
                if item["kind"] in _CONTAINER_KINDS:
                    render(item["items"], level + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")
        if index is not None and (isinstance(index, bool) or not isinstance(index, int)):
            raise ValueError("index must be an integer or None")

        found_item = None
        source_items = None
        source_index = None
        destination = self._data if destination_id == "workspace" else None

        def locate(items):
            nonlocal found_item, source_items, source_index, destination
            for position, item in enumerate(items):
                if item["id"] == item_id:
                    found_item = item
                    source_items = items
                    source_index = position
                if destination_id != "workspace" and item["id"] == destination_id:
                    destination = item
                if item["kind"] in _CONTAINER_KINDS:
                    locate(item["items"])

        locate(self._data["items"])

        if found_item is None or destination is None:
            raise ValueError("item or destination not found")
        if destination is not self._data and destination["kind"] not in _CONTAINER_KINDS:
            raise ValueError("destination is not a container")

        destination_items = destination["items"]
        if index is not None and not 0 <= index <= len(destination_items):
            raise ValueError("index out of range")

        if found_item["kind"] in _CONTAINER_KINDS:
            if destination is found_item:
                raise ValueError("cannot move a container into itself")
            for descendant in self._walk(found_item["items"]):
                if descendant is destination:
                    raise ValueError("cannot move a container into its descendant")

        source_items.pop(source_index)
        destination_items.insert(len(destination_items) if index is None else index, found_item)

    def to_data(self):
        return copy.deepcopy(self._data)
