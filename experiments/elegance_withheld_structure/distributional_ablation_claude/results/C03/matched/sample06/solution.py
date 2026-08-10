import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ------------------------------------------------------------------
    # Shared traversal: yields (item, depth) in list order, depth-first.
    # Top-level items are at nesting level 1.
    # ------------------------------------------------------------------
    def _walk(self):
        def visit(items, depth):
            for item in items:
                yield item, depth
                if item["kind"] in _CONTAINER_KINDS:
                    yield from visit(item["items"], depth + 1)

        yield from visit(self._data["items"], 1)

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------
    def total_word_count(self):
        total = 0
        for item, _depth in self._walk():
            if item["kind"] == "note":
                # Marker syntax and its identifier never count as words.
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item, _depth in self._walk():
            if item["kind"] == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                found.update(item["unresolved"])
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]
        for item, depth in self._walk():
            lines.append("  " * depth + "{}: {}".format(item["kind"], item["title"]))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Move
    # ------------------------------------------------------------------
    def _find(self, item_id):
        """Return (parent_list, index, item) or None."""

        def search(items):
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    return items, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    hit = search(item["items"])
                    if hit is not None:
                        return hit
            return None

        return search(self._data["items"])

    @staticmethod
    def _subtree_contains(item, target_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        stack = list(item["items"])
        while stack:
            current = stack.pop()
            if current["id"] == target_id:
                return True
            if current["kind"] in _CONTAINER_KINDS:
                stack.extend(current["items"])
        return False

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        found = self._find(item_id)
        if found is None:
            raise ValueError("unknown item id: {!r}".format(item_id))
        source_list, source_pos, item = found

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError("unknown destination id: {!r}".format(destination_id))
            dest_item = dest_found[2]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError(
                    "destination {!r} is not a container".format(destination_id)
                )
            if dest_item["id"] == item_id or self._subtree_contains(item, destination_id):
                raise ValueError(
                    "cannot move {!r} into itself or its descendants".format(item_id)
                )
            dest_list = dest_item["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if not (0 <= index <= len(dest_list)):
                raise ValueError("index {} out of range".format(index))

        # All validation passed; mutate atomically.
        source_list.pop(source_pos)
        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def to_data(self):
        return copy.deepcopy(self._data)
