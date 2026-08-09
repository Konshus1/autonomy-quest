import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}
_ITEM_KINDS = _CONTAINER_KINDS | {"note", "reference"}


class Workspace:
    def __init__(self, data):
        candidate = copy.deepcopy(data)
        self._validate_workspace(candidate)
        self._data = candidate

    @staticmethod
    def _validate_workspace(data):
        if not isinstance(data, dict):
            raise ValueError("workspace data must be a dictionary")
        if not isinstance(data.get("title"), str):
            raise ValueError("workspace title must be a string")
        if not isinstance(data.get("items"), list):
            raise ValueError("workspace items must be a list")

        seen_ids = set()

        def validate_item(item):
            if not isinstance(item, dict):
                raise ValueError("items must be dictionaries")

            item_id = item.get("id")
            kind = item.get("kind")

            if not isinstance(item_id, str):
                raise ValueError("item ids must be strings")
            if item_id in seen_ids:
                raise ValueError("item ids must be unique")
            if kind not in _ITEM_KINDS:
                raise ValueError("invalid item kind")
            if not isinstance(item.get("title"), str):
                raise ValueError("item titles must be strings")

            seen_ids.add(item_id)

            if kind in _CONTAINER_KINDS:
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
                    isinstance(word_count, bool)
                    or not isinstance(word_count, int)
                    or word_count < 0
                ):
                    raise ValueError(
                        "reference word_count must be a nonnegative integer"
                    )

                unresolved = item.get("unresolved")
                if (
                    not isinstance(unresolved, list)
                    or any(not isinstance(marker, str) for marker in unresolved)
                ):
                    raise ValueError(
                        "reference unresolved must be a list of strings"
                    )

        for item in data["items"]:
            validate_item(item)

    @staticmethod
    def _walk(items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from Workspace._walk(item["items"])

    def total_word_count(self):
        total = 0
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item in self._walk(self._data["items"]):
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
                    "  " * level + f"{item['kind']}: {item['title']}"
                )
                if item["kind"] in _CONTAINER_KINDS:
                    render(item["items"], level + 1)

        render(self._data["items"], 0)
        return "\n".join(lines)

    @staticmethod
    def _find(items, sought_id):
        for position, item in enumerate(items):
            if item["id"] == sought_id:
                return items, position, item
            if item["kind"] in _CONTAINER_KINDS:
                found = Workspace._find(item["items"], sought_id)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _contains_id(item, sought_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        for child in item["items"]:
            if child["id"] == sought_id:
                return True
            if Workspace._contains_id(child, sought_id):
                return True
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")
        if index is not None and (
            isinstance(index, bool) or not isinstance(index, int)
        ):
            raise ValueError("index must be an integer or None")

        candidate = copy.deepcopy(self._data)
        source = self._find(candidate["items"], item_id)
        if source is None:
            raise ValueError("item id not found")

        source_items, source_index, item = source

        if destination_id == "workspace":
            destination_items = candidate["items"]
        else:
            destination = self._find(candidate["items"], destination_id)
            if destination is None:
                raise ValueError("destination id not found")

            destination_item = destination[2]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            if destination_item is item or self._contains_id(item, destination_id):
                raise ValueError(
                    "a container cannot be moved into itself or a descendant"
                )
            destination_items = destination_item["items"]

        if index is not None and not 0 <= index <= len(destination_items):
            raise ValueError("index out of range")

        moved_item = source_items.pop(source_index)
        if index is None:
            destination_items.append(moved_item)
        else:
            destination_items.insert(index, moved_item)

        self._data = candidate

    def to_data(self):
        return copy.deepcopy(self._data)
