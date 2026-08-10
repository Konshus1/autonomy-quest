import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

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

        def render(items, depth):
            for item in items:
                lines.append("  " * depth + f"{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    render(item["items"], depth + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")
        found = self._find(item_id, self._data["items"])
        if found is None:
            raise ValueError(f"no item with id {item_id!r}")
        parent_items, pos, item = found
        dest_items = self._resolve_destination(destination_id)
        if item["kind"] in _CONTAINER_KINDS:
            subtree_ids = {node["id"] for node in self._walk([item])}
            if destination_id in subtree_ids:
                raise ValueError("cannot move a container into itself or its descendants")
        if index is not None:
            if (not isinstance(index, int) or isinstance(index, bool)
                    or index < 0 or index > len(dest_items)):
                raise ValueError(f"index {index!r} out of range for destination")
        parent_items.pop(pos)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)

    def _walk(self, items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"])

    def _find(self, item_id, items):
        for i, item in enumerate(items):
            if item["id"] == item_id:
                return items, i, item
            if item["kind"] in _CONTAINER_KINDS:
                found = self._find(item_id, item["items"])
                if found is not None:
                    return found
        return None

    def _resolve_destination(self, destination_id):
        if destination_id == "workspace":
            return self._data["items"]
        found = self._find(destination_id, self._data["items"])
        if found is None:
            raise ValueError(f"no destination with id {destination_id!r}")
        _, _, dest = found
        if dest["kind"] not in _CONTAINER_KINDS:
            raise ValueError(f"destination {destination_id!r} is not a container")
        return dest["items"]
