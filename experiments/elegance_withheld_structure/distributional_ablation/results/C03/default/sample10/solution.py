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
                    raise ValueError("each item must be a dictionary")

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
                        raise ValueError("reference word_count must be nonnegative")
                    unresolved = item.get("unresolved")
                    if (not isinstance(unresolved, list) or
                            any(not isinstance(marker, str) for marker in unresolved)):
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

        item = None
        source_items = None
        destination_items = None

        def search(items):
            nonlocal item, source_items, destination_items
            for candidate in items:
                if candidate["id"] == item_id:
                    item = candidate
                    source_items = items
                if candidate["id"] == destination_id:
                    if candidate["kind"] not in _CONTAINER_KINDS:
                        raise ValueError("destination is not a container")
                    destination_items = candidate["items"]
                if candidate["kind"] in _CONTAINER_KINDS:
                    search(candidate["items"])

        if destination_id == "workspace":
            destination_items = self._data["items"]
        search(self._data["items"])

        if item is None:
            raise ValueError("item id not found")
        if destination_items is None:
            raise ValueError("destination id not found")

        destination_length = len(destination_items)
        if index is not None and not 0 <= index <= destination_length:
            raise ValueError("index out of range")

        if item["kind"] in _CONTAINER_KINDS:
            if item["id"] == destination_id:
                raise ValueError("cannot move a container into itself")

            def contains_id(container, sought_id):
                for child in container["items"]:
                    if child["id"] == sought_id:
                        return True
                    if (child["kind"] in _CONTAINER_KINDS and
                            contains_id(child, sought_id)):
                        return True
                return False

            if destination_id != "workspace" and contains_id(item, destination_id):
                raise ValueError("cannot move a container into its descendant")

        source_items.remove(item)
        if index is None:
            destination_items.append(item)
        else:
            destination_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
