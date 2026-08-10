import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def to_data(self):
        return copy.deepcopy(self._data)

    def _iter_items(self, items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._iter_items(item["items"])

    def total_word_count(self):
        total = 0
        for item in self._iter_items(self._data["items"]):
            if item["kind"] == "note":
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item in self._iter_items(self._data["items"]):
            if item["kind"] == "note":
                markers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self):
        lines = [self._data["title"]]

        def render(items, level):
            for item in items:
                lines.append("  " * level + f"{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    render(item["items"], level + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def _find_parent(self, item_id):
        def search(items):
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    return items, i
                if item["kind"] in _CONTAINER_KINDS:
                    found = search(item["items"])
                    if found is not None:
                        return found
            return None

        return search(self._data["items"])

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination id must be a string")

        found = self._find_parent(item_id)
        if found is None:
            raise ValueError(f"no item with id {item_id!r}")
        parent_items, pos = found
        item = parent_items[pos]

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest = None
            for candidate in self._iter_items(self._data["items"]):
                if candidate["id"] == destination_id:
                    dest = candidate
                    break
            if dest is None:
                raise ValueError(f"no item with id {destination_id!r}")
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"destination {destination_id!r} is not a container")
            if dest is item:
                raise ValueError("cannot move a container into itself")
            if item["kind"] in _CONTAINER_KINDS and any(
                descendant is dest for descendant in self._iter_items(item["items"])
            ):
                raise ValueError("cannot move a container into its own descendant")
            dest_items = dest["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer")
            if index < 0 or index > len(dest_items):
                raise ValueError(f"index {index} out of range")

        parent_items.pop(pos)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)
