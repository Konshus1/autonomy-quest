"""Workspace model: sections, groups, notes, and references, with word
totals, unresolved-marker collection, outlining, and validated moves."""

import copy
import re
import unittest

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ------------------------------------------------------------------
    # Traversal

    def _walk(self):
        """Yield (item, containing_list, depth) in document order."""

        def rec(items, depth):
            for item in items:
                yield item, items, depth
                if item["kind"] in _CONTAINER_KINDS:
                    yield from rec(item["items"], depth + 1)

        yield from rec(self._data["items"], 1)

    @staticmethod
    def _subtree_contains(container, target):
        if container["kind"] in _CONTAINER_KINDS:
            for child in container["items"]:
                if child is target or Workspace._subtree_contains(child, target):
                    return True
        return False

    # ------------------------------------------------------------------
    # Queries

    def total_word_count(self):
        total = 0
        for item, _, _ in self._walk():
            if item["kind"] == "note":
                stripped = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(stripped))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        identifiers = set()
        for item, _, _ in self._walk():
            if item["kind"] == "note":
                identifiers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                identifiers.update(item["unresolved"])
        return sorted(identifiers)

    def outline(self):
        lines = [self._data["title"]]
        for item, _, depth in self._walk():
            lines.append("  " * depth + "%s: %s" % (item["kind"], item["title"]))
        return "\n".join(lines)

    def to_data(self):
        return copy.deepcopy(self._data)

    # ------------------------------------------------------------------
    # Mutation

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        item = None
        source_list = None
        for node, containing, _ in self._walk():
            if node["id"] == item_id:
                item, source_list = node, containing
                break
        if item is None:
            raise ValueError("no item with id %r" % item_id)

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest_node = None
            for node, _, _ in self._walk():
                if node["id"] == destination_id:
                    dest_node = node
                    break
            if dest_node is None:
                raise ValueError("no destination with id %r" % destination_id)
            if dest_node["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination %r is not a container" % destination_id)
            if dest_node is item or self._subtree_contains(item, dest_node):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_list = dest_node["items"]

        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_list):
                raise ValueError("index %d out of range" % index)

        # All validation passed; mutate. Remove by identity so equal-valued
        # sibling dictionaries cannot be confused.
        for pos, node in enumerate(source_list):
            if node is item:
                del source_list[pos]
                break

        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)


# ----------------------------------------------------------------------
# Tests


def sample_data():
    return {
        "title": "Research",
        "items": [
            {
                "id": "s1",
                "kind": "section",
                "title": "Intro",
                "items": [
                    {
                        "id": "n1",
                        "kind": "note",
                        "title": "Overview",
                        "text": "Alpha beta [[?src1]] gamma.",
                    },
                    {
                        "id": "r1",
                        "kind": "reference",
                        "title": "Paper A",
                        "label": "A",
                        "word_count": 10,
                        "unresolved": ["cite2", "src1"],
                    },
                ],
            },
            {
                "id": "g1",
                "kind": "group",
                "title": "Extras",
                "items": [
                    {
                        "id": "g2",
                        "kind": "group",
                        "title": "Deep",
                        "items": [
                            {
                                "id": "n2",
                                "kind": "note",
                                "title": "Idea",
                                "text": "delta [[?x9]]",
                            }
                        ],
                    }
                ],
            },
        ],
    }


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(sample_data())

    def test_constructor_deep_copies(self):
        data = sample_data()
        ws = Workspace(data)
        data["items"][0]["items"][0]["text"] = "changed completely"
        self.assertEqual(ws.total_word_count(), 14)

    def test_total_word_count(self):
        # n1: Alpha beta gamma = 3, n2: delta = 1, r1 declares 10.
        self.assertEqual(self.ws.total_word_count(), 14)

    def test_marker_does_not_fuse_words(self):
        ws = Workspace(
            {
                "title": "T",
                "items": [
                    {"id": "n", "kind": "note", "title": "N", "text": "foo[[?m]]bar"}
                ],
            }
        )
        self.assertEqual(ws.total_word_count(), 2)
        self.assertEqual(ws.unresolved_markers(), ["m"])

    def test_unresolved_markers_sorted_unique(self):
        self.assertEqual(self.ws.unresolved_markers(), ["cite2", "src1", "x9"])

    def test_outline(self):
        expected = "\n".join(
            [
                "Research",
                "  section: Intro",
                "    note: Overview",
                "    reference: Paper A",
                "  group: Extras",
                "    group: Deep",
                "      note: Idea",
            ]
        )
        self.assertEqual(self.ws.outline(), expected)

    def test_move_reparents_with_index(self):
        self.ws.move("n1", "g2", 0)
        deep = self.ws.to_data()["items"][1]["items"][0]["items"]
        self.assertEqual([item["id"] for item in deep], ["n1", "n2"])
        self.assertEqual(self.ws.total_word_count(), 14)

    def test_move_appends_when_index_none(self):
        self.ws.move("r1", "workspace")
        top = [item["id"] for item in self.ws.to_data()["items"]]
        self.assertEqual(top, ["s1", "g1", "r1"])

    def test_move_reorders_within_same_container(self):
        self.ws.move("r1", "s1", 0)
        section = self.ws.to_data()["items"][0]["items"]
        self.assertEqual([item["id"] for item in section], ["r1", "n1"])

    def assert_move_rejected(self, *args):
        before = self.ws.to_data()
        with self.assertRaises(ValueError):
            self.ws.move(*args)
        self.assertEqual(self.ws.to_data(), before)

    def test_rejections_leave_workspace_unchanged(self):
        self.assert_move_rejected("missing", "workspace")
        self.assert_move_rejected("n1", "missing")
        self.assert_move_rejected("n1", "n2")  # note is not a container
        self.assert_move_rejected("g1", "g1")  # into itself
        self.assert_move_rejected("g1", "g2")  # into own descendant
        self.assert_move_rejected("n1", "workspace", 3)  # out of range
        self.assert_move_rejected("n1", "workspace", -1)
        self.assert_move_rejected(7, "workspace")  # non-string id
        self.assert_move_rejected("n1", 7)

    def test_to_data_returns_deep_copy(self):
        snapshot = self.ws.to_data()
        snapshot["items"].clear()
        self.assertEqual(len(self.ws.to_data()["items"]), 2)


if __name__ == "__main__":
    unittest.main()
