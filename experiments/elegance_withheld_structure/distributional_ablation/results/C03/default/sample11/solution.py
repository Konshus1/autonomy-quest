import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\]]+)\]\]")
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
                        raise ValueError("notes must have text")
                else:
                    count = item.get("word_count")
                    unresolved = item.get("unresolved")
                    if not isinstance(item.get("label"), str):
                        raise ValueError("references must have a label")
                    if (not isinstance(count, int) or isinstance(count, bool)
                            or count < 0):
                        raise ValueError("reference word_count must be nonnegative")
                    if (not isinstance(unresolved, list)
                            or any(not isinstance(marker, str)
                                   for marker in unresolved)):
                        raise ValueError("reference unresolved must be strings")

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
        if index is not None and (
            not isinstance(index, int) or isinstance(index, bool)
        ):
            raise ValueError("index must be an integer or None")

        locations = {}

        def index_items(items):
            for item in items:
                locations[item["id"]] = (item, items)
                if item["kind"] in _CONTAINER_KINDS:
                    index_items(item["items"])

        index_items(self._data["items"])

        if item_id not in locations:
            raise ValueError("item not found")
        item, source = locations[item_id]

        if destination_id == "workspace":
            destination = self._data["items"]
        else:
            destination_entry = locations.get(destination_id)
            if destination_entry is None:
                raise ValueError("destination not found")
            destination_item = destination_entry[0]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination = destination_item["items"]

        destination_length = len(destination)
        if index is not None and not 0 <= index <= destination_length:
            raise ValueError("index out of range")

        if item["kind"] in _CONTAINER_KINDS and destination_id != "workspace":
            descendant_ids = {
                descendant["id"] for descendant in self._walk(item["items"])
            }
            if destination_id == item_id or destination_id in descendant_ids:
                raise ValueError("move would create invalid containment")

        source.remove(item)
        if index is None:
            destination.append(item)
        else:
            destination.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
