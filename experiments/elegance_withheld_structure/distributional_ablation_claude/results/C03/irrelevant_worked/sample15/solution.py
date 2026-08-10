import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ---- queries -----------------------------------------------------

    def total_word_count(self):
        total = 0
        for item in self._iter_items():
            if item["kind"] == "note":
                # Replace markers with a space so surrounding words are
                # not fused together, and identifiers are never counted.
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        identifiers = set()
        for item in self._iter_items():
            if item["kind"] == "note":
                identifiers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                identifiers.update(item["unresolved"])
        return sorted(identifiers)

    def outline(self):
        lines = [self._data["title"]]

        def walk(items, depth):
            for item in items:
                lines.append("  " * depth + f"{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    walk(item["items"], depth + 1)

        walk(self._data["items"], 1)
        return "\n".join(lines)

    def to_data(self):
        return copy.deepcopy(self._data)

    # ---- mutation ----------------------------------------------------

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")
        if index is not None and (isinstance(index, bool) or not isinstance(index, int)):
            raise ValueError("index must be None or an integer")

        found = self._find(item_id)
        if found is None:
            raise ValueError(f"no item with id {item_id!r}")
        parent_items, position, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
            if self._subtree_contains(item, destination_id):
                raise ValueError("cannot move an item into itself or its descendants")
        else:
            dest_found = self._find(destination_id)
            if dest_found is None:
                raise ValueError(f"no item with id {destination_id!r}")
            destination = dest_found[2]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"destination {destination_id!r} is not a container")
            if self._subtree_contains(item, destination_id):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_items = destination["items"]

        if index is not None and not 0 <= index <= len(dest_items):
            raise ValueError(f"index {index} out of range for destination")

        # All checks passed; mutation from here on cannot fail.
        parent_items.pop(position)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    # ---- helpers -----------------------------------------------------

    def _iter_items(self):
        def walk(items):
            for item in items:
                yield item
                if item["kind"] in _CONTAINER_KINDS:
                    yield from walk(item["items"])

        yield from walk(self._data["items"])

    def _find(self, item_id):
        def search(items):
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    return items, i, item
                if item["kind"] in _CONTAINER_KINDS:
                    result = search(item["items"])
                    if result is not None:
                        return result
            return None

        return search(self._data["items"])

    @staticmethod
    def _subtree_contains(item, target_id):
        if item["id"] == target_id:
            return True
        if item["kind"] in _CONTAINER_KINDS:
            return any(
                Workspace._subtree_contains(child, target_id)
                for child in item["items"]
            )
        return False
