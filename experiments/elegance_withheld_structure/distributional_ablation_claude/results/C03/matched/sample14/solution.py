"""Workspace composite: sections and groups contain notes, references, and nested groups."""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ---- summaries -------------------------------------------------

    def total_word_count(self):
        return sum(self._count_words(item) for item in self._data["items"])

    def _count_words(self, item):
        kind = item["kind"]
        if kind in _CONTAINER_KINDS:
            return sum(self._count_words(child) for child in item["items"])
        if kind == "note":
            text = _MARKER_RE.sub(" ", item["text"])
            return len(_WORD_RE.findall(text))
        return item["word_count"]

    def unresolved_markers(self):
        found = set()

        def visit(item):
            kind = item["kind"]
            if kind in _CONTAINER_KINDS:
                for child in item["items"]:
                    visit(child)
            elif kind == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            else:
                found.update(item["unresolved"])

        for item in self._data["items"]:
            visit(item)
        return sorted(found)

    # ---- outline ---------------------------------------------------

    def outline(self):
        lines = [self._data["title"]]

        def visit(item, level):
            lines.append("  " * level + f"{item['kind']}: {item['title']}")
            if item["kind"] in _CONTAINER_KINDS:
                for child in item["items"]:
                    visit(child, level + 1)

        for item in self._data["items"]:
            visit(item, 1)
        return "\n".join(lines)

    # ---- move ------------------------------------------------------

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")

        source_items, pos = self._locate(item_id)
        if source_items is None:
            raise ValueError(f"no item with id {item_id!r}")
        item = source_items[pos]

        if destination_id == "workspace":
            dest_items = self._data["items"]
            if self._subtree_contains(item, dest_items):
                raise ValueError("cannot move an item into its own subtree")
        else:
            dest_list, dest_pos = self._locate(destination_id)
            if dest_list is None:
                raise ValueError(f"no item with id {destination_id!r}")
            dest = dest_list[dest_pos]
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"destination {destination_id!r} is not a container")
            if dest is item or self._subtree_contains(item, [dest]):
                raise ValueError("cannot move an item into itself or its own subtree")
            dest_items = dest["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_items):
                raise ValueError(f"index {index} out of range")

        del source_items[pos]
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    def _locate(self, item_id):
        def search(items):
            for i, it in enumerate(items):
                if it["id"] == item_id:
                    return items, i
                if it["kind"] in _CONTAINER_KINDS:
                    found = search(it["items"])
                    if found is not None:
                        return found
            return None

        return search(self._data["items"]) or (None, None)

    def _subtree_contains(self, item, targets):
        if item["kind"] not in _CONTAINER_KINDS:
            return False
        stack = [item]
        while stack:
            current = stack.pop()
            for target in targets:
                if current["items"] is target or current is target:
                    return True
            for child in current["items"]:
                if child["kind"] in _CONTAINER_KINDS:
                    stack.append(child)
                for target in targets:
                    if child is target:
                        return True
        return False

    # ---- export ----------------------------------------------------

    def to_data(self):
        return copy.deepcopy(self._data)
