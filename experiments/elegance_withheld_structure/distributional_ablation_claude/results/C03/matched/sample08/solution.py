"""Workspaces of sections, notes, references, and nested groups.

The workspace state is a plain dict tree; sections and groups contain an
"items" list, notes and references are leaves.  All summaries are recursive
traversals, and move() validates everything before mutating so a failed move
never changes the workspace.
"""

import copy
import re

_MARKER_RE = re.compile(r"\\[\\[\\?([^\\[\\]]+)\\]\\]")
_WORD_RE = re.compile(r"\\b\\w+\\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ----- summaries -------------------------------------------------

    def total_word_count(self):
        return sum(self._word_count(item) for item in self._data["items"])

    def _word_count(self, item):
        kind = item["kind"]
        if kind == "note":
            # Replace markers with a space so words on either side of a
            # marker are not merged into one run.
            text = _MARKER_RE.sub(" ", item["text"])
            return len(_WORD_RE.findall(text))
        if kind == "reference":
            return item["word_count"]
        return sum(self._word_count(child) for child in item["items"])

    def unresolved_markers(self):
        found = set()

        def visit(item):
            kind = item["kind"]
            if kind == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif kind == "reference":
                found.update(item["unresolved"])
            else:
                for child in item["items"]:
                    visit(child)

        for item in self._data["items"]:
            visit(item)
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]

        def visit(item, level):
            lines.append("  " * level + f"{item['kind']}: {item['title']}")
            if item["kind"] in _CONTAINER_KINDS:
                for child in item["items"]:
                    visit(child, level + 1)

        for item in self._data["items"]:
            visit(item, 1)
        return "\\n".join(lines)

    # ----- move ------------------------------------------------------

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        located = self._find_parent(item_id)
        if located is None:
            raise ValueError(f"no item with id {item_id!r}")
        parent_items, pos = located
        item = parent_items[pos]

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            located = self._find_parent(destination_id)
            if located is None:
                raise ValueError(f"no item with id {destination_id!r}")
            dest = located[0][located[1]]
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError(f"{destination_id!r} is not a container")
            if destination_id in self._subtree_ids(item):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_items = dest["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_items):
                raise ValueError(f"index {index} out of range")

        # All checks passed; mutate only now.
        parent_items.pop(pos)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

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

    def _subtree_ids(self, item):
        ids = {item["id"]}
        if item["kind"] in _CONTAINER_KINDS:
            for child in item["items"]:
                ids.update(self._subtree_ids(child))
        return ids

    # ----- export ----------------------------------------------------

    def to_data(self):
        return copy.deepcopy(self._data)
