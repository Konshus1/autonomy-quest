import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


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
                    raise ValueError("items must be dictionaries")

                item_id = item.get("id")
                if not isinstance(item_id, str):
                    raise ValueError("item ids must be strings")
                if item_id in seen:
                    raise ValueError("item ids must be unique")
                seen.add(item_id)

                if not isinstance(item.get("title"), str):
                    raise ValueError("item titles must be strings")

                kind = item.get("kind")
                if kind in _CONTAINER_KINDS:
                    if not isinstance(item.get("items"), list):
                        raise ValueError("containers must have an items list")
                    validate_items(item["items"])
                elif kind == "note":
                    if not isinstance(item.get("text"), str):
                        raise ValueError("notes must have string text")
                elif kind == "reference":
                    if not isinstance(item.get("label"), str):
                        raise ValueError("references must have a string label")
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
                        raise ValueError("reference unresolved must be a list of strings")
                else:
                    raise ValueError("unknown item kind")

        validate_items(self._data["items"])

    def _walk(self, items=None):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"])

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
        identifiers = set()
        for item in self._walk():
            if item["kind"] == "note":
                identifiers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                identifiers.update(item["unresolved"])
        return sorted(identifiers)

    def outline(self):
        lines = [self._data["title"]]

        def render(items, level):
            for item in items:
                lines.append(
                    "  " * level + "{}: {}".format(item["kind"], item["title"])
                )
                if item["kind"] in _CONTAINER_KINDS:
                    render(item["items"], level + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")

        source = self._locate(item_id)
        if source is None:
            raise ValueError("item id not found")
        source_items, source_index, item = source

        if destination_id == "workspace":
            destination_items = self._data["items"]
        else:
            destination = self._locate(destination_id)
            if destination is None:
                raise ValueError("destination id not found")
            destination_item = destination[2]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination_item["items"]

        if item["kind"] in _CONTAINER_KINDS:
            descendant_ids = {node["id"] for node in self._walk([item])}
            if destination_id in descendant_ids:
                raise ValueError("move would create a containment cycle")

        destination_length = len(destination_items)
        if index is not None:
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index > destination_length
            ):
                raise ValueError("index is out of range")

        moved_item = source_items.pop(source_index)
        if index is None:
            destination_items.append(moved_item)
        else:
            destination_items.insert(index, moved_item)

    def to_data(self):
        return copy.deepcopy(self._data)
