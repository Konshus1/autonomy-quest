import copy
import re


_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = frozenset({"section", "group"})


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ---- traversal -------------------------------------------------

    def _walk(self, items, depth):
        for item in items:
            yield item, depth
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"], depth + 1)

    def _find(self, item_id):
        """Return (parent_list, index, item) for item_id, or None."""
        stack = [self._data["items"]]
        while stack:
            lst = stack.pop()
            for i, item in enumerate(lst):
                if item["id"] == item_id:
                    return lst, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    stack.append(item["items"])
        return None

    @staticmethod
    def _subtree_contains(item, target_id):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        stack = list(item["items"])
        while stack:
            child = stack.pop()
            if child["id"] == target_id:
                return True
            if child["kind"] in _CONTAINER_KINDS:
                stack.extend(child["items"])
        return False

    # ---- queries ---------------------------------------------------

    def total_word_count(self):
        total = 0
        for item, _ in self._walk(self._data["items"], 1):
            if item["kind"] == "note":
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        identifiers = set()
        for item, _ in self._walk(self._data["items"], 1):
            if item["kind"] == "note":
                identifiers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                identifiers.update(item["unresolved"])
        return sorted(identifiers)

    def outline(self):
        lines = [self._data["title"]]
        for item, depth in self._walk(self._data["items"], 1):
            lines.append("  " * depth + "{}: {}".format(item["kind"], item["title"]))
        return "\n".join(lines)

    # ---- mutation --------------------------------------------------

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")

        found = self._find(item_id)
        if found is None:
            raise ValueError("no item with id {!r}".format(item_id))
        source_list, source_index, item = found

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError("no destination with id {!r}".format(destination_id))
            dest_item = dest_found[2]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError(
                    "destination {!r} is not a container".format(destination_id)
                )
            if destination_id == item_id or self._subtree_contains(item, destination_id):
                raise ValueError("cannot move a container into itself or its contents")
            dest_list = dest_item["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_list):
                raise ValueError("index {} out of range".format(index))

        del source_list[source_index]
        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)
