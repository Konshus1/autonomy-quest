"""Workspace model: sections, groups, notes, and references with
aggregate queries and validated, atomic moves.

Only the Python standard library is used.
"""

import copy
import re
import unittest

_MARKER = re.compile(r"\[\[\?(\w+)\]\]")
_WORD = re.compile(r"\b\w+\b")
_CONTAINERS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # -- queries -----------------------------------------------------------

    def total_word_count(self):
        total = 0
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                # Markers and their identifiers must not count as words.
                total += len(_WORD.findall(_MARKER.sub(" ", item["text"])))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        markers = set()
        for item in self._walk(self._data["items"]):
            if item["kind"] == "note":
                markers.update(_MARKER.findall(item["text"]))
            elif item["kind"] == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self):
        lines = [self._data["title"]]

        def render(items, depth):
            for item in items:
                lines.append("  " * depth + "%s: %s" % (item["kind"], item["title"]))
                if item["kind"] in _CONTAINERS:
                    render(item["items"], depth + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def to_data(self):
        return copy.deepcopy(self._data)

    # -- move --------------------------------------------------------------

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")
        located = self._locate(item_id)
        if located is None:
            raise ValueError("no item with id %r" % item_id)
        parent_items, pos, item = located

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_located = self._locate(destination_id)
            if dest_located is None:
                raise ValueError("no destination with id %r" % destination_id)
            dest = dest_located[2]
            if dest["kind"] not in _CONTAINERS:
                raise ValueError("destination %r is not a container" % destination_id)
            if dest is item or any(d is dest for d in self._walk(item.get("items", []))):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_items = dest["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_items):
                raise ValueError("index %d out of range" % index)

        # All validation passed; mutation below cannot fail.
        del parent_items[pos]
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    # -- helpers -----------------------------------------------------------

    def _walk(self, items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINERS:
                yield from self._walk(item["items"])

    def _locate(self, item_id):
        """Return (parent_items, position, item) for item_id, or None."""
        stack = [self._data["items"]]
        while stack:
            items = stack.pop()
            for pos, item in enumerate(items):
                if item["id"] == item_id:
                    return items, pos, item
                if item["kind"] in _CONTAINERS:
                    stack.append(item["items"])
        return None


# -- tests ------------------------------------------------------------------


def _sample():
    return {
        "title": "Research",
        "items": [
            {"id": "s1", "kind": "section", "title": "Intro", "items": [
                {"id": "n1", "kind": "note", "title": "Note One",
                 "text": "Hello world [[?todo]] again"},
                {"id": "g1", "kind": "group", "title": "Sources", "items": [
                    {"id": "r1", "kind": "reference", "title": "Paper",
                     "label": "P1", "word_count": 5,
                     "unresolved": ["cite", "todo"]},
                ]},
            ]},
            {"id": "n2", "kind": "note", "title": "Loose", "text": "one two three"},
        ],
    }


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(_sample())

    def test_total_word_count_excludes_markers(self):
        # n1 -> 3 (marker identifier ignored), n2 -> 3, r1 -> 5
        self.assertEqual(self.ws.total_word_count(), 11)

    def test_unresolved_markers_unique_sorted(self):
        self.assertEqual(self.ws.unresolved_markers(), ["cite", "todo"])

    def test_outline(self):
        self.assertEqual(self.ws.outline(), "\n".join([
            "Research",
            "  section: Intro",
            "    note: Note One",
            "    group: Sources",
            "      reference: Paper",
            "  note: Loose",
        ]))

    def test_to_data_is_deep_copy(self):
        snapshot = self.ws.to_data()
        snapshot["items"].clear()
        self.assertEqual(self.ws.to_data(), _sample())

    def test_constructor_deep_copies_input(self):
        data = _sample()
        ws = Workspace(data)
        data["items"].clear()
        self.assertEqual(ws.to_data(), _sample())

    def test_move_append_and_indexed_insert(self):
        self.ws.move("n2", "g1")
        self.assertEqual(
            [i["id"] for i in self.ws.to_data()["items"][0]["items"][1]["items"]],
            ["r1", "n2"])
        self.ws.move("n2", "workspace", 0)
        self.assertEqual([i["id"] for i in self.ws.to_data()["items"]],
                         ["n2", "s1"])
        # Subtree contents survive the moves untouched.
        self.assertEqual(self.ws.total_word_count(), 11)

    def _assert_move_fails_unchanged(self, *args, **kwargs):
        before = self.ws.to_data()
        with self.assertRaises(ValueError):
            self.ws.move(*args, **kwargs)
        self.assertEqual(self.ws.to_data(), before)

    def test_failed_moves_leave_workspace_unchanged(self):
        self._assert_move_fails_unchanged("missing", "workspace")
        self._assert_move_fails_unchanged("n1", "missing")
        self._assert_move_fails_unchanged("n1", "n2")            # non-container
        self._assert_move_fails_unchanged("s1", "s1")            # into itself
        self._assert_move_fails_unchanged("s1", "g1")            # into descendant
        self._assert_move_fails_unchanged("n2", "g1", index=2)   # out of range
        self._assert_move_fails_unchanged("n2", "g1", index=-1)
        self._assert_move_fails_unchanged(5, "workspace")        # non-string ids
        self._assert_move_fails_unchanged("n2", 5)


if __name__ == "__main__":
    unittest.main()
