import copy
import operator
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self._validate_workspace(self._data)

    @staticmethod
    def _validate_workspace(data):
        if not isinstance(data, dict):
            raise ValueError("workspace data must be a dictionary")
        if not isinstance(data.get("title"), str):
            raise ValueError("workspace title must be a string")
        if not isinstance(data.get("items"), list):
            raise ValueError("workspace items must be a list")

        seen_ids = set()

        def validate_items(items):
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("each item must be a dictionary")

                item_id = item.get("id")
                if not isinstance(item_id, str) or item_id in seen_ids:
                    raise ValueError("item ids must be unique strings")
                seen_ids.add(item_id)

                kind = item.get("kind")
                if kind not in {"section", "group", "note", "reference"}:
                    raise ValueError("invalid item kind")
                if not isinstance(item.get("title"), str):
                    raise ValueError("item title must be a string")

                if kind in _CONTAINER_KINDS:
                    children = item.get("items")
                    if not isinstance(children, list):
                        raise ValueError("containers must have an items list")
                    validate_items(children)
                elif kind == "note":
                    if not isinstance(item.get("text"), str):
                        raise ValueError("notes must have string text")
                else:
                    if not isinstance(item.get("label"), str):
                        raise ValueError("references must have a string label")
                    word_count = item.get("word_count")
                    if type(word_count) is not int or word_count < 0:
                        raise ValueError("reference word_count must be nonnegative")
                    unresolved = item.get("unresolved")
                    if not isinstance(unresolved, list) or not all(
                        isinstance(marker, str) for marker in unresolved
                    ):
                        raise ValueError("reference unresolved must be a list of strings")

        validate_items(data["items"])

    def _walk(self, items=None, depth=0):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item, depth
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    def total_word_count(self):
        total = 0
        for item, _ in self._walk():
            if item["kind"] == "note":
                text_without_markers = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text_without_markers))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item, _ in self._walk():
            if item["kind"] == "note":
                markers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self):
        lines = [self._data["title"]]
        for item, depth in self._walk():
            lines.append("  " * depth + f"{item['kind']}: {item['title']}")
        return "\n".join(lines)

    def _find_item(self, item_id):
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

    @staticmethod
    def _contains_id(container, sought_id):
        if container["kind"] not in _CONTAINER_KINDS:
            return False
        for child in container["items"]:
            if child["id"] == sought_id:
                return True
            if Workspace._contains_id(child, sought_id):
                return True
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")

        source = self._find_item(item_id)
        if source is None:
            raise ValueError("item does not exist")
        item, source_items, source_index = source

        if destination_id == "workspace":
            destination_items = self._data["items"]
        else:
            destination = self._find_item(destination_id)
            if destination is None:
                raise ValueError("destination does not exist")
            destination_item = destination[0]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            if destination_item is item or self._contains_id(item, destination_id):
                raise ValueError("move would create a containment cycle")
            destination_items = destination_item["items"]

        if index is None:
            insertion_index = len(destination_items)
        else:
            try:
                insertion_index = operator.index(index)
            except TypeError as exc:
                raise ValueError("index must be an integer") from exc
            if not 0 <= insertion_index <= len(destination_items):
                raise ValueError("index out of range")

        source_items.pop(source_index)
        destination_items.insert(insertion_index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
