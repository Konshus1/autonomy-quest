"""Workspace tree of sections, groups, notes, and references.

Standard library only.
"""

import copy
import re

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ------------------------------------------------------------------ #
    # queries

    def total_word_count(self):
        total = 0
        for item in self._iter_items():
            kind = item["kind"]
            if kind == "note":
                # Marker syntax and its identifier never count as words.
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif kind == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item in self._iter_items():
            kind = item["kind"]
            if kind == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif kind == "reference":
                found.update(item["unresolved"])
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]

        def render(items, level):
            indent = "  " * level
            for item in items:
                lines.append(f"{indent}{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    render(item["items"], level + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def to_data(self):
        return copy.deepcopy(self._data)

    # ------------------------------------------------------------------ #
    # mutation

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")

        found = self._find_with_parent(item_id)
        if found is None:
            raise ValueError(f"no item with id {item_id!r}")
        item, source_list = found

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest_found = self._find_with_parent(destination_id)
            if dest_found is None:
                raise ValueError(f"no destination with id {destination_id!r}")
            dest_item = dest_found[0]
            if dest_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError(
                    f"destination {destination_id!r} is not a container"
                )
            if dest_item is item or self._subtree_contains(item, dest_item):
                raise ValueError(
                    "cannot move a container into itself or its descendants"
                )
            dest_list = dest_item["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_list):
                raise ValueError(f"index {index} out of range")

        # All validation passed; the tree is mutated only from here on.
        for pos, candidate in enumerate(source_list):
            if candidate is item:
                del source_list[pos]
                break
        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)

    # ------------------------------------------------------------------ #
    # internals

    def _iter_items(self):
        stack = list(reversed(self._data["items"]))
        while stack:
            item = stack.pop()
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                stack.extend(reversed(item["items"]))

    def _find_with_parent(self, item_id):
        def search(items):
            for item in items:
                if item["id"] == item_id:
                    return item, items
                if item["kind"] in _CONTAINER_KINDS:
                    hit = search(item["items"])
                    if hit is not None:
                        return hit
            return None

        return search(self._data["items"])

    @staticmethod
    def _subtree_contains(root, target):
        if root["kind"] not in _CONTAINER_KINDS:
            return False
        stack = list(root["items"])
        while stack:
            item = stack.pop()
            if item is target:
                return True
            if item["kind"] in _CONTAINER_KINDS:
                stack.extend(item["items"])
        return False


if __name__ == "__main__":
    import unittest

    def sample():
        return {
            "title": "Research",
            "items": [
                {
                    "id": "s1",
                    "kind": "section",
                    "title": "Background",
                    "items": [
                        {
                            "id": "n1",
                            "kind": "note",
                            "title": "Intro",
                            "text": "Alpha beta [[?src1]] gamma.",
                        },
                        {
                            "id": "g1",
                            "kind": "group",
                            "title": "Sources",
                            "items": [
                                {
                                    "id": "r1",
                                    "kind": "reference",
                                    "title": "Paper A",
                                    "label": "A",
                                    "word_count": 10,
                                    "unresolved": ["src2", "src1"],
                                }
                            ],
                        },
                    ],
                },
                {
                    "id": "n2",
                    "kind": "note",
                    "title": "Loose",
                    "text": "one two three",
                },
            ],
        }

    class WorkspaceTests(unittest.TestCase):
        def setUp(self):
            self.ws = Workspace(sample())

        def test_total_word_count(self):
            # note n1: alpha, beta, gamma (marker excluded) = 3
            # reference r1: 10; note n2: 3
            self.assertEqual(self.ws.total_word_count(), 16)

        def test_unresolved_markers(self):
            self.assertEqual(self.ws.unresolved_markers(), ["src1", "src2"])

        def test_outline(self):
            self.assertEqual(
                self.ws.outline(),
                "Research\n"
                "  section: Background\n"
                "    note: Intro\n"
                "    group: Sources\n"
                "      reference: Paper A\n"
                "  note: Loose",
            )

        def test_constructor_deep_copies(self):
            raw = sample()
            ws = Workspace(raw)
            raw["items"][0]["items"].clear()
            self.assertIn("note: Intro", ws.outline())

        def test_to_data_is_deep_copy(self):
            snap = self.ws.to_data()
            snap["items"].clear()
            self.assertEqual(len(self.ws.to_data()["items"]), 2)

        def test_move_to_workspace_with_index(self):
            self.ws.move("n1", "workspace", 0)
            data = self.ws.to_data()
            self.assertEqual(data["items"][0]["id"], "n1")
            self.assertEqual(
                [i["id"] for i in data["items"][1]["items"]], ["g1"]
            )

        def test_move_appends_when_index_none(self):
            self.ws.move("n2", "g1")
            group = self.ws.to_data()["items"][0]["items"][1]
            self.assertEqual([i["id"] for i in group["items"]], ["r1", "n2"])

        def test_move_preserves_descendants(self):
            self.ws.move("g1", "workspace")
            self.assertEqual(self.ws.total_word_count(), 16)
            self.assertEqual(self.ws.unresolved_markers(), ["src1", "src2"])

        def assert_move_fails_unchanged(self, *args, **kwargs):
            before = self.ws.to_data()
            with self.assertRaises(ValueError):
                self.ws.move(*args, **kwargs)
            self.assertEqual(self.ws.to_data(), before)

        def test_missing_ids(self):
            self.assert_move_fails_unchanged("nope", "workspace")
            self.assert_move_fails_unchanged("n1", "nope")

        def test_non_string_ids(self):
            self.assert_move_fails_unchanged(3, "workspace")
            self.assert_move_fails_unchanged("n1", None)

        def test_non_container_destination(self):
            self.assert_move_fails_unchanged("n2", "n1")
            self.assert_move_fails_unchanged("n2", "r1")

        def test_cycle_rejected(self):
            self.assert_move_fails_unchanged("s1", "s1")
            self.assert_move_fails_unchanged("s1", "g1")

        def test_index_out_of_range(self):
            self.assert_move_fails_unchanged("n2", "g1", 2)
            self.assert_move_fails_unchanged("n2", "g1", -1)

    unittest.main()
