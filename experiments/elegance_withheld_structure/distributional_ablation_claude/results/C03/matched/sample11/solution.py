import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ---- summaries ----

    def total_word_count(self):
        return sum(self._item_word_count(item) for item in self._data["items"])

    def _item_word_count(self, item):
        kind = item["kind"]
        if kind == "note":
            text = _MARKER_RE.sub(" ", item["text"])
            return len(_WORD_RE.findall(text))
        if kind == "reference":
            return item["word_count"]
        return sum(self._item_word_count(child) for child in item["items"])

    def unresolved_markers(self):
        found = set()

        def visit(item):
            kind = item["kind"]
            if kind == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif kind == "reference":
                found.update(item["unresolved"])
            else:
                for child in item["items"]:
                    visit(child)

        for item in self._data["items"]:
            visit(item)
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]

        def visit(item, depth):
            lines.append("  " * depth + f"{item['kind']}: {item['title']}")
            if item["kind"] in _CONTAINER_KINDS:
                for child in item["items"]:
                    visit(child, depth + 1)

        for item in self._data["items"]:
            visit(item, 1)
        return "\n".join(lines)

    # ---- structure ----

    def _locate(self, item_id):
        def search(items):
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    return items, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    found = search(item["items"])
                    if found is not None:
                        return found
            return None

        return search(self._data["items"])

    def _contains(self, item, target):
        if item is target:
            return True
        if item["kind"] in _CONTAINER_KINDS:
            return any(self._contains(child, target) for child in item["items"])
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        located = self._locate(item_id)
        if located is None:
            raise ValueError(f"no item with id {item_id!r}")
        source_items, source_index, item = located

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_located = self._locate(destination_id)
            if dest_located is None:
                raise ValueError(f"no item with id {destination_id!r}")
            dest_item = dest_located[2]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"{destination_id!r} is not a container")
            if self._contains(item, dest_item):
                raise ValueError(
                    "cannot move a container into itself or its descendants"
                )
            dest_items = dest_item["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_items):
                raise ValueError(f"index {index} out of range")

        source_items.pop(source_index)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
