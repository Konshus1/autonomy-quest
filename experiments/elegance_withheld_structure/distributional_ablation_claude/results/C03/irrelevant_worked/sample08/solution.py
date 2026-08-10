import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    """A tree of sections, groups, notes, and references under one title."""

    def __init__(self, data):
        self._data = copy.deepcopy(data)

    def total_word_count(self):
        total = 0
        for item, _depth in self._walk():
            if item["kind"] == "note":
                # Markers and their identifiers never count as words.
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        idents = set()
        for item, _depth in self._walk():
            if item["kind"] == "note":
                idents.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                for marker in item["unresolved"]:
                    m = _MARKER_RE.fullmatch(marker)
                    idents.add(m.group(1) if m else marker)
        return sorted(idents)

    def outline(self):
        lines = [self._data["title"]]
        for item, depth in self._walk():
            lines.append("  " * depth + f"{item['kind']}: {item['title']}")
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")
        found = self._find(item_id)
        if found is None:
            raise ValueError(f"no item with id {item_id!r}")
        parent_items, pos, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError(f"no item with id {destination_id!r}")
            dest = dest_found[2]
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"destination {destination_id!r} is not a container")
            if destination_id == item_id or self._contains(item, destination_id):
                raise ValueError(
                    "cannot move a container into itself or its descendants"
                )
            dest_items = dest["items"]

        if index is not None:
            if not isinstance(index, int) or not (0 <= index <= len(dest_items)):
                raise ValueError(f"invalid insertion index {index!r}")

        # All validation passed; mutate only now so failed moves change nothing.
        parent_items.pop(pos)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def to_data(self):
        return copy.deepcopy(self._data)

    def _walk(self):
        def visit(items, depth):
            for item in items:
                yield item, depth
                if item["kind"] in _CONTAINER_KINDS:
                    yield from visit(item["items"], depth + 1)

        yield from visit(self._data["items"], 1)

    def _find(self, item_id):
        def search(items):
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    return items, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    found = search(item["items"])
                    if found is not None:
                        return found
            return None

        return search(self._data["items"])

    @staticmethod
    def _contains(item, target_id):
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
