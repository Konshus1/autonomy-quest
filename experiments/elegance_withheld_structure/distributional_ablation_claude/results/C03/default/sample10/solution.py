import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # -- aggregation -------------------------------------------------

    def total_word_count(self):
        total = 0
        for item in self._iter_items():
            if item["kind"] == "note":
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item in self._iter_items():
            if item["kind"] == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                found.update(item["unresolved"])
        return sorted(found)

    # -- rendering ---------------------------------------------------

    def outline(self):
        lines = [self._data["title"]]

        def walk(items, depth):
            for item in items:
                lines.append("  " * depth + f"{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    walk(item["items"], depth + 1)

        walk(self._data["items"], 1)
        return "\n".join(lines)

    # -- structural edits --------------------------------------------

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        located = self._find_with_parent(item_id, self._data["items"])
        if located is None:
            raise ValueError(f"no item with id {item_id!r}")
        item, source_items = located

        dest_items = self._container_items(destination_id)

        if item["kind"] in _CONTAINER_KINDS:
            if destination_id == item_id or self._contains(item, destination_id):
                raise ValueError(
                    "cannot move a container into itself or its descendants"
                )

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer")
            if not 0 <= index <= len(dest_items):
                raise ValueError(f"index {index} out of range")

        for i, candidate in enumerate(source_items):
            if candidate is item:
                del source_items[i]
                break

        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)

    # -- internals ---------------------------------------------------

    def _iter_items(self, items=None):
        if items is None:
            items = self._data["items"]
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._iter_items(item["items"])

    def _find_with_parent(self, item_id, items):
        for item in items:
            if item["id"] == item_id:
                return item, items
            if item["kind"] in _CONTAINER_KINDS:
                found = self._find_with_parent(item_id, item["items"])
                if found is not None:
                    return found
        return None

    def _container_items(self, destination_id):
        if destination_id == "workspace":
            return self._data["items"]
        located = self._find_with_parent(destination_id, self._data["items"])
        if located is None:
            raise ValueError(f"no item with id {destination_id!r}")
        destination = located[0]
        if destination["kind"] not in _CONTAINER_KINDS:
            raise ValueError(f"{destination_id!r} is not a container")
        return destination["items"]

    def _contains(self, container, target_id):
        for child in container["items"]:
            if child["id"] == target_id:
                return True
            if child["kind"] in _CONTAINER_KINDS and self._contains(child, target_id):
                return True
        return False
