import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def _walk(self):
        """Yield (item, parent_list, index) for every item in document order."""

        def visit(items):
            for i, item in enumerate(items):
                yield item, items, i
                if item["kind"] in _CONTAINER_KINDS:
                    yield from visit(item["items"])

        yield from visit(self._data["items"])

    def total_word_count(self):
        total = 0
        for item, _, _ in self._walk():
            if item["kind"] == "note":
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item, _, _ in self._walk():
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

    def _find(self, item_id):
        for item, parent_list, index in self._walk():
            if item["id"] == item_id:
                return item, parent_list, index
        return None

    @staticmethod
    def _subtree_contains(container, target):
        if container["kind"] not in _CONTAINER_KINDS:
            return False
        stack = list(container["items"])
        while stack:
            current = stack.pop()
            if current is target:
                return True
            if current["kind"] in _CONTAINER_KINDS:
                stack.extend(current["items"])
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")

        found = self._find(item_id)
        if found is None:
            raise ValueError(f"no item with id {item_id!r}")
        item, source_list, source_index = found

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError(f"no item with id {destination_id!r}")
            destination = dest_found[0]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError(
                    f"destination {destination_id!r} is not a container"
                )
            if destination is item:
                raise ValueError("cannot move an item into itself")
            if self._subtree_contains(item, destination):
                raise ValueError("cannot move an item into its own descendant")
            dest_list = destination["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_list):
                raise ValueError(f"index {index} out of range")

        source_list.pop(source_index)
        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
