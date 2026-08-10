"""Workspace tree: sections, groups, notes, and references.

Public API:
    Workspace(data)      -- data is {"title": str, "items": list}, deep-copied
    total_word_count()   -- int
    unresolved_markers() -- sorted unique list of marker identifiers
    outline()            -- deterministic indented outline string
    move(item_id, destination_id, index=None)
    to_data()            -- deep copy of current state
"""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ------------------------------------------------------------------ queries

    def total_word_count(self):
        total = 0
        for item in self._iter_items():
            if item["kind"] == "note":
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
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

    def outline(self):
        lines = [self._data["title"]]

        def walk(items, depth):
            for item in items:
                lines.append("  " * depth + "%s: %s" % (item["kind"], item["title"]))
                if item["kind"] in _CONTAINER_KINDS:
                    walk(item["items"], depth + 1)

        walk(self._data["items"], 1)
        return "\n".join(lines)

    def to_data(self):
        return copy.deepcopy(self._data)

    # ---------------------------------------------------------------- mutation

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")
        if index is not None and not isinstance(index, int):
            raise ValueError("index must be an int or None")

        found = self._find(item_id, self._data["items"])
        if found is None:
            raise ValueError("no item with id %r" % item_id)
        source_items, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_found = self._find(destination_id, self._data["items"])
            if dest_found is None:
                raise ValueError("no destination with id %r" % destination_id)
            _, dest = dest_found
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination %r is not a container" % destination_id)
            if dest is item or self._contains(item, dest):
                raise ValueError("cannot move an item into itself or a descendant")
            dest_items = dest["items"]

        if index is not None and not (0 <= index <= len(dest_items)):
            raise ValueError("index %d out of range" % index)

        # All checks passed; mutate.
        for i, node in enumerate(source_items):
            if node is item:
                del source_items[i]
                break
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    # ----------------------------------------------------------------- helpers

    def _iter_items(self):
        stack = list(self._data["items"])
        while stack:
            item = stack.pop()
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                stack.extend(item["items"])

    def _find(self, item_id, items):
        for item in items:
            if item["id"] == item_id:
                return items, item
            if item["kind"] in _CONTAINER_KINDS:
                found = self._find(item_id, item["items"])
                if found is not None:
                    return found
        return None

    def _contains(self, ancestor, node):
        if ancestor["kind"] not in _CONTAINER_KINDS:
            return False
        for child in ancestor["items"]:
            if child is node or self._contains(child, node):
                return True
        return False


if __name__ == "__main__":
    import unittest

    def sample():
        return {
            "title": "Research",
            "items": [
                {"id": "s1", "kind": "section", "title": "Background", "items": [
                    {"id": "n1", "kind": "note", "title": "Intro",
                     "text": "Early results [[?fig1]] were promising."},
                    {"id": "g1", "kind": "group", "title": "Sources", "items": [
                        {"id": "r1", "kind": "reference", "title": "Survey",
                         "label": "SRV", "word_count": 120,
                         "unresolved": ["doi", "fig1"]},
                    ]},
                ]},
                {"id": "n2", "kind": "note", "title": "Todo",
                 "text": "check [[?doi]]"},
            ],
        }

    class WorkspaceTests(unittest.TestCase):
        def setUp(self):
            self.ws = Workspace(sample())

        def test_total_word_count(self):
            # n1: 4 words after marker removal, n2: 1 word, r1: 120 declared.
            self.assertEqual(self.ws.total_word_count(), 125)

        def test_unresolved_markers(self):
            self.assertEqual(self.ws.unresolved_markers(), ["doi", "fig1"])

        def test_outline(self):
            expected = "\n".join([
                "Research",
                "  section: Background",
                "    note: Intro",
                "    group: Sources",
                "      reference: Survey",
                "  note: Todo",
            ])
            self.assertEqual(self.ws.outline(), expected)

        def test_constructor_deep_copies(self):
            data = sample()
            ws = Workspace(data)
            data["items"].clear()
            data["title"] = "mutated"
            self.assertEqual(ws.to_data()["title"], "Research")
            self.assertEqual(len(ws.to_data()["items"]), 2)

        def test_to_data_returns_deep_copy(self):
            snap = self.ws.to_data()
            snap["items"][0]["items"].clear()
            self.assertEqual(len(self.ws.to_data()["items"][0]["items"]), 2)

        def test_move_append(self):
            self.ws.move("n2", "g1")
            group = self.ws.to_data()["items"][0]["items"][1]
            self.assertEqual([i["id"] for i in group["items"]], ["r1", "n2"])
            self.assertEqual(len(self.ws.to_data()["items"]), 1)
            self.assertEqual(self.ws.total_word_count(), 125)

        def test_move_with_index_to_workspace(self):
            self.ws.move("n1", "workspace", 0)
            self.assertEqual(
                [i["id"] for i in self.ws.to_data()["items"]],
                ["n1", "s1", "n2"])

        def test_move_container_preserves_descendants(self):
            self.ws.move("g1", "workspace")
            moved = self.ws.to_data()["items"][-1]
            self.assertEqual(moved["id"], "g1")
            self.assertEqual(moved["items"][0]["id"], "r1")
            self.assertEqual(self.ws.unresolved_markers(), ["doi", "fig1"])

        def assert_move_fails_unchanged(self, *args):
            before = self.ws.to_data()
            with self.assertRaises(ValueError):
                self.ws.move(*args)
            self.assertEqual(self.ws.to_data(), before)

        def test_missing_item_id(self):
            self.assert_move_fails_unchanged("nope", "g1")

        def test_missing_destination_id(self):
            self.assert_move_fails_unchanged("n2", "nope")

        def test_non_container_destination(self):
            self.assert_move_fails_unchanged("n2", "n1")

        def test_move_into_self(self):
            self.assert_move_fails_unchanged("g1", "g1")

        def test_move_into_own_descendant(self):
            self.assert_move_fails_unchanged("s1", "g1")

        def test_index_out_of_range(self):
            self.assert_move_fails_unchanged("n2", "g1", 2)
            self.assert_move_fails_unchanged("n2", "g1", -1)

        def test_non_string_ids(self):
            self.assert_move_fails_unchanged(3, "g1")
            self.assert_move_fails_unchanged("n2", None)

    unittest.main()
