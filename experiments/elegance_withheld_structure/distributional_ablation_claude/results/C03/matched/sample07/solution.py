import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def total_word_count(self):
        def count(items):
            total = 0
            for item in items:
                kind = item["kind"]
                if kind == "note":
                    text = _MARKER_RE.sub(" ", item["text"])
                    total += len(_WORD_RE.findall(text))
                elif kind == "reference":
                    total += item["word_count"]
                else:
                    total += count(item["items"])
            return total

        return count(self._data["items"])

    def unresolved_markers(self):
        found = set()

        def walk(items):
            for item in items:
                kind = item["kind"]
                if kind == "note":
                    found.update(_MARKER_RE.findall(item["text"]))
                elif kind == "reference":
                    found.update(item["unresolved"])
                else:
                    walk(item["items"])

        walk(self._data["items"])
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]

        def walk(items, depth):
            for item in items:
                lines.append("  " * depth + f"{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    walk(item["items"], depth + 1)

        walk(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        located = self._locate(item_id)
        if located is None:
            raise ValueError(f"no item with id {item_id!r}")
        source_items, position, item = located

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_located = self._locate(destination_id)
            if dest_located is None:
                raise ValueError(f"no destination with id {destination_id!r}")
            dest_item = dest_located[2]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError(
                    f"destination {destination_id!r} is not a container"
                )
            if destination_id in self._subtree_ids(item):
                raise ValueError(
                    "cannot move an item into itself or its descendants"
                )
            dest_items = dest_item["items"]

        if index is not None and not (0 <= index <= len(dest_items)):
            raise ValueError(f"index {index!r} out of range")

        source_items.pop(position)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)

    def _locate(self, target_id):
        def walk(items):
            for i, item in enumerate(items):
                if item["id"] == target_id:
                    return items, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    hit = walk(item["items"])
                    if hit is not None:
                        return hit
            return None

        return walk(self._data["items"])

    def _subtree_ids(self, item):
        ids = {item["id"]}
        if item["kind"] in _CONTAINER_KINDS:
            for child in item["items"]:
                ids |= self._subtree_ids(child)
        return ids
