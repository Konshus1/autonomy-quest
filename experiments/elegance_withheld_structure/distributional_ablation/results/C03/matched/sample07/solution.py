import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]\r\n]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}
_ITEM_KINDS = _CONTAINER_KINDS | {"note", "reference"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self._validate_data(self._data)

    @staticmethod
    def _validate_data(data):
        if not isinstance(data, dict):
            raise ValueError("workspace data must be a dictionary")
        if not isinstance(data.get("title"), str):
            raise ValueError("workspace title must be a string")
        if not isinstance(data.get("items"), list):
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
                raise ValueError("invalid item kind")
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

        for root_item in data["items"]:
            validate_item(root_item)

    @staticmethod
    def _walk(items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from Workspace._walk(item["items"])

    @staticmethod
    def _find(items, item_id):
        for index, item in enumerate(items):
            if item["id"] == item_id:
                return item, items, index
            if item["kind"] in _CONTAINER_KINDS:
                found = Workspace._find(item["items"], item_id)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _contains(container, item_id):
        if container["kind"] not in _CONTAINER_KINDS:
            return False
        return any(item["id"] == item_id for item in Workspace._walk(container["items"]))

    def total_word_count(self):
        total = 0
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                text_without_markers = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text_without_markers))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        identifiers = set()
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                identifiers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                identifiers.update(item["unresolved"])
        return sorted(identifiers)

    def outline(self):
        lines = [self._data["title"]]

        def add_lines(items, level):
            for item in items:
                lines.append(
                    "  " * level + "{}: {}".format(item["kind"], item["title"])
                )
                if item["kind"] in _CONTAINER_KINDS:
                    add_lines(item["items"], level + 1)

        add_lines(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")
        if index is not None and (
            not isinstance(index, int) or isinstance(index, bool)
        ):
            raise ValueError("index must be an integer or None")

        source_result = self._find(self._data["items"], item_id)
        if source_result is None:
            raise ValueError("item id not found")
        source_item = source_result[0]

        if destination_id == "workspace":
            destination_items = self._data["items"]
        else:
            destination_result = self._find(self._data["items"], destination_id)
            if destination_result is None:
                raise ValueError("destination id not found")
            destination = destination_result[0]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination["items"]

            if destination_id == item_id or self._contains(source_item, destination_id):
                raise ValueError("move would create invalid containment")

        if index is not None and not 0 <= index <= len(destination_items):
            raise ValueError("index out of range")

        candidate = copy.deepcopy(self._data)
        candidate_source = self._find(candidate["items"], item_id)
        moved_item = candidate_source[1].pop(candidate_source[2])

        if destination_id == "workspace":
            candidate_destination_items = candidate["items"]
        else:
            candidate_destination = self._find(candidate["items"], destination_id)
            candidate_destination_items = candidate_destination[0]["items"]

        insertion_index = len(candidate_destination_items) if index is None else index
        candidate_destination_items.insert(insertion_index, moved_item)
        self._data = candidate

    def to_data(self):
        return copy.deepcopy(self._data)
