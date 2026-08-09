import copy
import operator
import re

__all__ = ["Workspace"]


class Workspace:
    _MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
    _WORD_RE = re.compile(r"\b\w+\b")
    _CONTAINER_KINDS = {"section", "group"}
    _ITEM_KINDS = _CONTAINER_KINDS | {"note", "reference"}

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
            if kind not in self._ITEM_KINDS:
                raise ValueError("invalid item kind")
            if not isinstance(item.get("title"), str):
                raise ValueError("item titles must be strings")

            if kind in self._CONTAINER_KINDS:
                children = item.get("items")
                if not isinstance(children, list):
                    raise ValueError("containers must have an items list")
                for child in children:
                    validate_item(child)
            elif kind == "note":
                if not isinstance(item.get("text"), str):
                    raise ValueError("notes must have string text")
            else:
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
                    raise ValueError("reference unresolved markers must be strings")

        for item in self._data["items"]:
            validate_item(item)

    @classmethod
    def _walk(cls, items):
        for item in items:
            yield item
            if item["kind"] in cls._CONTAINER_KINDS:
                yield from cls._walk(item["items"])

    def total_word_count(self):
        total = 0
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                text_without_markers = self._MARKER_RE.sub("", item["text"])
                total += len(self._WORD_RE.findall(text_without_markers))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        identifiers = set()
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                identifiers.update(self._MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                for marker in item["unresolved"]:
                    match = self._MARKER_RE.fullmatch(marker)
                    identifiers.add(match.group(1) if match else marker)
        return sorted(identifiers)

    def outline(self):
        lines = [self._data["title"]]

        def append_items(items, level):
            for item in items:
                lines.append(
                    f"{'  ' * level}{item['kind']}: {item['title']}"
                )
                if item["kind"] in self._CONTAINER_KINDS:
                    append_items(item["items"], level + 1)

        append_items(self._data["items"], 1)
        return "\n".join(lines)

    def _find(self, item_id):
        def search(items):
            for position, item in enumerate(items):
                if item["id"] == item_id:
                    return item, items, position
                if item["kind"] in self._CONTAINER_KINDS:
                    found = search(item["items"])
                    if found is not None:
                        return found
            return None

        return search(self._data["items"])

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        source = self._find(item_id)
        if source is None:
            raise ValueError("item id does not exist")
        item, source_items, source_index = source

        if destination_id == "workspace":
            destination_items = self._data["items"]
        else:
            destination = self._find(destination_id)
            if destination is None:
                raise ValueError("destination id does not exist")
            destination_item = destination[0]
            if destination_item["kind"] not in self._CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination_item["items"]

            if item["kind"] in self._CONTAINER_KINDS:
                descendant_ids = {
                    descendant["id"] for descendant in self._walk([item])
                }
                if destination_id in descendant_ids:
                    raise ValueError("move would create invalid containment")

        if index is None:
            insertion_index = len(destination_items)
        else:
            try:
                insertion_index = operator.index(index)
            except TypeError as exc:
                raise ValueError("index must be an integer") from exc
            if not 0 <= insertion_index <= len(destination_items):
                raise ValueError("index is out of range")

        moved_item = source_items.pop(source_index)
        destination_items.insert(insertion_index, moved_item)

    def to_data(self):
        return copy.deepcopy(self._data)
