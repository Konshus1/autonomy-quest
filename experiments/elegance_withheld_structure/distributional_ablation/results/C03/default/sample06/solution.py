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
                    visit(item["items"])
                elif kind == "note":
                    if not isinstance(item.get("text"), str):
                        raise ValueError("notes must have string text")
                else:
                    if not isinstance(item.get("label"), str):
                        raise ValueError("references must have a string label")
                    count = item.get("word_count")
                    if type(count) is not int or count < 0:
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
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
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
                lines.append("  " * level + f'{item["kind"]}: {item["title"]}')
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

        source = self._locate(item_id)
        if source is None:
            raise ValueError("item id not found")
        item, source_items, source_index = source

        if destination_id == "workspace":
            destination_items = self._data["items"]
            destination = None
        else:
            located = self._locate(destination_id)
            if located is None:
                raise ValueError("destination id not found")
            destination = located[0]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination["items"]

        if index is not None:
            if type(index) is not int or not 0 <= index <= len(destination_items):
                raise ValueError("index out of range")

        if destination is item:
            raise ValueError("cannot move a container into itself")
        if item["kind"] in _CONTAINER_KINDS and destination is not None:
            if any(descendant is destination for descendant in self._walk(item["items"])):
                raise ValueError("cannot move a container into its descendant")

        source_items.pop(source_index)
        if index is None:
            destination_items.append(item)
        else:
            destination_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
