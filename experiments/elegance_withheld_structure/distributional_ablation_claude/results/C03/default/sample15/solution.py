import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def _walk(self, items, depth=1):
        for item in items:
            yield item, depth
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    def _locate(self, items, item_id):
        for i, item in enumerate(items):
            if item["id"] == item_id:
                return items, i, item
            if item["kind"] in _CONTAINER_KINDS:
                found = self._locate(item["items"], item_id)
                if found is not None:
                    return found
        return None

    def _contains(self, container, target):
        if container["kind"] not in _CONTAINER_KINDS:
            return False
        for child, _ in self._walk(container["items"]):
            if child is target:
                return True
        return False

    def total_word_count(self):
        total = 0
        for item, _ in self._walk(self._data["items"]):
            if item["kind"] == "note":
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item, _ in self._walk(self._data["items"]):
            if item["kind"] == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                found.update(item["unresolved"])
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]
        for item, depth in self._walk(self._data["items"]):
            lines.append("  " * depth + f"{item['kind']}: {item['title']}")
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError(f"item id must be a string, got {type(item_id).__name__}")
        if not isinstance(destination_id, str):
            raise ValueError(
                f"destination id must be a string, got {type(destination_id).__name__}"
            )

        located = self._locate(self._data["items"], item_id)
        if located is None:
            raise ValueError(f"no item with id {item_id!r}")
        source_list, source_index, item = located

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_located = self._locate(self._data["items"], destination_id)
            if dest_located is None:
                raise ValueError(f"no item with id {destination_id!r}")
            _, _, dest = dest_located
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError(
                    f"destination {destination_id!r} is a {dest['kind']}, not a container"
                )
            if dest is item or self._contains(item, dest):
                raise ValueError(
                    f"cannot move {item_id!r} into itself or its own descendant"
                )
            dest_items = dest["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_items):
                raise ValueError(
                    f"index {index} out of range for destination of length {len(dest_items)}"
                )

        source_list.pop(source_index)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
