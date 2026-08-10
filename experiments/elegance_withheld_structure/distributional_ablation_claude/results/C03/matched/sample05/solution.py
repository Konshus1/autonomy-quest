import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def total_word_count(self):
        def walk(items):
            total = 0
            for item in items:
                kind = item["kind"]
                if kind == "note":
                    # Marker tokens and their identifiers never count as words;
                    # replace with a space so markers cannot merge adjacent words.
                    text = _MARKER_RE.sub(" ", item["text"])
                    total += len(_WORD_RE.findall(text))
                elif kind == "reference":
                    total += item["word_count"]
                else:
                    total += walk(item["items"])
            return total

        return walk(self._data["items"])

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

        def walk(items, level):
            for item in items:
                lines.append("  " * level + f"{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    walk(item["items"], level + 1)

        walk(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        found = self._find(item_id)
        if found is None:
            raise ValueError(f"unknown item id: {item_id!r}")
        parent_items, pos, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError(f"unknown destination id: {destination_id!r}")
            dest = dest_found[2]
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"destination is not a container: {destination_id!r}")
            if destination_id == item_id or self._contains(item, destination_id):
                raise ValueError("cannot move a container into itself or its descendants")
            dest_items = dest["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError(f"index must be an integer or None: {index!r}")
            if not 0 <= index <= len(dest_items):
                raise ValueError(f"index out of range: {index!r}")

        # All validation passed; only now mutate.
        parent_items.pop(pos)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)

    def _find(self, item_id):
        def walk(items):
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    return items, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    hit = walk(item["items"])
                    if hit is not None:
                        return hit
            return None

        return walk(self._data["items"])

    def _contains(self, item, item_id):
        for child in item.get("items", ()):
            if child["id"] == item_id:
                return True
            if child["kind"] in _CONTAINER_KINDS and self._contains(child, item_id):
                return True
        return False
