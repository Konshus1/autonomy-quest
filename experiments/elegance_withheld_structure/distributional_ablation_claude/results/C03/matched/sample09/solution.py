import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ---- summaries -------------------------------------------------

    def total_word_count(self):
        return sum(self._words(item) for item in self._data["items"])

    def _words(self, item):
        kind = item["kind"]
        if kind == "note":
            # Markers and their identifiers must not count as words, but
            # words adjacent to a marker remain separate words.
            text = _MARKER_RE.sub(" ", item["text"])
            return len(_WORD_RE.findall(text))
        if kind == "reference":
            return item["word_count"]
        return sum(self._words(child) for child in item["items"])

    def unresolved_markers(self):
        found = set()
        self._collect_markers(self._data["items"], found)
        return sorted(found)

    def _collect_markers(self, items, found):
        for item in items:
            kind = item["kind"]
            if kind == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif kind == "reference":
                found.update(item["unresolved"])
            else:
                self._collect_markers(item["items"], found)

    def outline(self):
        lines = [self._data["title"]]
        self._outline_items(self._data["items"], 1, lines)
        return "\n".join(lines)

    def _outline_items(self, items, level, lines):
        for item in items:
            lines.append("  " * level + "%s: %s" % (item["kind"], item["title"]))
            if item["kind"] in _CONTAINER_KINDS:
                self._outline_items(item["items"], level + 1, lines)

    # ---- structure -------------------------------------------------

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        located = self._locate(self._data["items"], item_id)
        if located is None:
            raise ValueError("no item with id %r" % item_id)
        parent_items, position, item = located

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_located = self._locate(self._data["items"], destination_id)
            if dest_located is None:
                raise ValueError("no destination with id %r" % destination_id)
            dest_item = dest_located[2]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination %r is not a container" % destination_id)
            if self._subtree_contains(item, destination_id):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_items = dest_item["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_items):
                raise ValueError("index %r out of range" % index)

        parent_items.pop(position)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def _locate(self, items, target_id):
        for position, item in enumerate(items):
            if item["id"] == target_id:
                return items, position, item
            if item["kind"] in _CONTAINER_KINDS:
                found = self._locate(item["items"], target_id)
                if found is not None:
                    return found
        return None

    def _subtree_contains(self, item, target_id):
        if item["id"] == target_id:
            return True
        if item["kind"] in _CONTAINER_KINDS:
            return any(self._subtree_contains(child, target_id)
                       for child in item["items"])
        return False

    def to_data(self):
        return copy.deepcopy(self._data)
