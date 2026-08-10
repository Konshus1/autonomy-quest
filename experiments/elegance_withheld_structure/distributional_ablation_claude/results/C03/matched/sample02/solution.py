import copy
import re
import unittest

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    """A tree of sections, groups, notes, and references.

    The constructor deep-copies its input; to_data() returns a deep copy,
    so external code can never alias internal state.
    """

    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    def total_word_count(self):
        def count(items):
            total = 0
            for item in items:
                kind = item["kind"]
                if kind == "note":
                    # Marker syntax and its identifier never count as words.
                    text = _MARKER_RE.sub(" ", item["text"])
                    total += len(_WORD_RE.findall(text))
                elif kind == "reference":
                    total += item["word_count"]
                else:
                    total += count(item["items"])
            return total

        return count(self._data["items"])

    def unresolved_markers(self):
        found = set()

        def walk(items):
            for item in items:
                kind = item["kind"]
                if kind == "note":
                    found.update(_MARKER_RE.findall(item["text"]))
                elif kind == "reference":
                    found.update(item["unresolved"])
                else:
                    walk(item["items"])

        walk(self._data["items"])
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

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination id must be a string")

        found = self._find_with_parent(self._data["items"], item_id)
        if found is None:
            raise ValueError("no item with id %r" % item_id)
        parent_items, position, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest = self._find(self._data["items"], destination_id)
            if dest is None:
                raise ValueError("no destination with id %r" % destination_id)
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination %r is not a container" % destination_id)
            if destination_id in self._subtree_ids(item):
                raise ValueError(
                    "cannot move %r into itself or its own contents" % item_id
                )
            dest_items = dest["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(dest_items):
                raise ValueError("index %d out of range" % index)

        # All validation passed; only now is the tree touched.
        parent_items.pop(position)
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _find(items, item_id):
        for item in items:
            if item["id"] == item_id:
                return item
            if item["kind"] in _CONTAINER_KINDS:
                hit = Workspace._find(item["items"], item_id)
                if hit is not None:
                    return hit
        return None

    @staticmethod
    def _find_with_parent(items, item_id):
        for position, item in enumerate(items):
            if item["id"] == item_id:
                return items, position, item
            if item["kind"] in _CONTAINER_KINDS:
                hit = Workspace._find_with_parent(item["items"], item_id)
                if hit is not None:
                    return hit
        return None

    @staticmethod
    def _subtree_ids(item):
        ids = {item["id"]}
        if item["kind"] in _CONTAINER_KINDS:
            for child in item["items"]:
                ids |= Workspace._subtree_ids(child)
        return ids


# ---------------------------------------------------------------------- #
# Tests (in-memory, standard library only)
# ---------------------------------------------------------------------- #


def _sample():
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
                        "text": "Alpha beta [[?src1]] gamma",
                    },
                    {
                        "id": "g1",
                        "kind": "group",
                        "title": "Papers",
                        "items": [
                            {
                                "id": "r1",
                                "kind": "reference",
                                "title": "Survey",
                                "label": "Doe 2024",
                                "word_count": 10,
                                "unresolved": ["cite2", "cite1"],
                            }
                        ],
                    },
                ],
            },
            {
                "id": "n2",
                "kind": "note",
                "title": "Todo",
                "text": "One two three [[?src1]] [[?extra]]",
            },
        ],
    }


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(_sample())

    def test_total_word_count(self):
        # notes: 3 + 3 words (markers excluded), reference: 10
        self.assertEqual(self.ws.total_word_count(), 16)

    def test_unresolved_markers_sorted_unique(self):
        self.assertEqual(
            self.ws.unresolved_markers(), ["cite1", "cite2", "extra", "src1"]
        )

    def test_outline(self):
        expected = "\n".join(
            [
                "Research",
                "  section: Background",
                "    note: Intro",
                "    group: Papers",
                "      reference: Survey",
                "  note: Todo",
            ]
        )
        self.assertEqual(self.ws.outline(), expected)

    def test_move_to_group_appends(self):
        self.ws.move("n2", "g1")
        data = self.ws.to_data()
        group = data["items"][0]["items"][1]
        self.assertEqual([i["id"] for i in group["items"]], ["r1", "n2"])
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(self.ws.total_word_count(), 16)

    def test_move_to_workspace_at_index(self):
        self.ws.move("r1", "workspace", 0)
        data = self.ws.to_data()
        self.assertEqual([i["id"] for i in data["items"]], ["r1", "s1", "n2"])
        self.assertEqual(data["items"][0]["label"], "Doe 2024")

    def test_failed_moves_leave_workspace_unchanged(self):
        before = self.ws.to_data()
        cases = [
            ("ghost", "g1", None),  # missing item
            ("n1", "ghost", None),  # missing destination
            ("n1", "n2", None),  # non-container destination
            ("n1", "g1", 5),  # out-of-range index
            ("n1", "g1", -1),  # negative index
            ("s1", "s1", None),  # container into itself
            ("s1", "g1", None),  # container into its own descendant
            (7, "g1", None),  # non-string item id
            ("n1", 7, None),  # non-string destination id
        ]
        for item_id, dest_id, index in cases:
            with self.assertRaises(ValueError):
                self.ws.move(item_id, dest_id, index)
            self.assertEqual(self.ws.to_data(), before)

    def test_move_preserves_descendants(self):
        self.ws.move("s1", "workspace", 1)
        data = self.ws.to_data()
        self.assertEqual([i["id"] for i in data["items"]], ["n2", "s1"])
        moved = data["items"][1]
        self.assertEqual([i["id"] for i in moved["items"]], ["n1", "g1"])
        self.assertEqual(self.ws.total_word_count(), 16)

    def test_to_data_is_deep_copy_and_input_is_copied(self):
        raw = _sample()
        ws = Workspace(raw)
        raw["items"].clear()
        self.assertEqual(len(ws.to_data()["items"]), 2)
        snapshot = ws.to_data()
        snapshot["items"].clear()
        self.assertEqual(len(ws.to_data()["items"]), 2)


if __name__ == "__main__":
    unittest.main()
