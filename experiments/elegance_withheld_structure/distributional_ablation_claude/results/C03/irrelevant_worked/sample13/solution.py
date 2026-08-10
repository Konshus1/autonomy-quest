import copy
import re
import unittest

_MARKER_RE = re.compile(r"\[\[\?(\w+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # -- read operations ---------------------------------------------------

    def total_word_count(self):
        total = 0
        for item in self._iter_items(self._data["items"]):
            if item["kind"] == "note":
                text = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self):
        found = set()
        for item in self._iter_items(self._data["items"]):
            if item["kind"] == "note":
                found.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                found.update(item["unresolved"])
        return sorted(found)

    def outline(self):
        lines = [self._data["title"]]

        def render(items, depth):
            for item in items:
                lines.append("  " * depth + "{}: {}".format(item["kind"], item["title"]))
                if item["kind"] in _CONTAINER_KINDS:
                    render(item["items"], depth + 1)

        render(self._data["items"], 1)
        return "\n".join(lines)

    def to_data(self):
        return copy.deepcopy(self._data)

    # -- mutation ----------------------------------------------------------

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item_id and destination_id must be strings")

        found = self._find_with_parent(self._data["items"], item_id)
        if found is None:
            raise ValueError("no item with id {!r}".format(item_id))
        item, source_list = found

        if destination_id == "workspace":
            dest_list = self._data["items"]
        else:
            dest = self._find(self._data["items"], destination_id)
            if dest is None:
                raise ValueError("no item with id {!r}".format(destination_id))
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError("{!r} is not a container".format(destination_id))
            if dest is item or self._subtree_contains(item, dest):
                raise ValueError("cannot move an item into itself or its descendants")
            dest_list = dest["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_list):
                raise ValueError("index out of range")

        for i, entry in enumerate(source_list):
            if entry is item:
                del source_list[i]
                break
        if index is None:
            dest_list.append(item)
        else:
            dest_list.insert(index, item)

    # -- helpers -----------------------------------------------------------

    def _iter_items(self, items):
        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._iter_items(item["items"])

    def _find(self, items, item_id):
        for item in self._iter_items(items):
            if item["id"] == item_id:
                return item
        return None

    def _find_with_parent(self, items, item_id):
        for item in items:
            if item["id"] == item_id:
                return item, items
            if item["kind"] in _CONTAINER_KINDS:
                found = self._find_with_parent(item["items"], item_id)
                if found is not None:
                    return found
        return None

    def _subtree_contains(self, root, target):
        if root["kind"] not in _CONTAINER_KINDS:
            return False
        for item in self._iter_items(root["items"]):
            if item is target:
                return True
        return False


# -- tests ------------------------------------------------------------------

def _sample():
    return {
        "title": "Research",
        "items": [
            {"id": "n1", "kind": "note", "title": "Intro",
             "text": "Alpha beta [[?todo1]] gamma."},
            {"id": "s1", "kind": "section", "title": "Body", "items": [
                {"id": "r1", "kind": "reference", "title": "Paper",
                 "label": "P1", "word_count": 10, "unresolved": ["cite1", "todo1"]},
                {"id": "g1", "kind": "group", "title": "Drafts", "items": [
                    {"id": "n2", "kind": "note", "title": "Draft",
                     "text": "one two[[?x]]three"},
                ]},
            ]},
        ],
    }


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(_sample())

    def test_constructor_deep_copies(self):
        data = _sample()
        ws = Workspace(data)
        data["items"].clear()
        self.assertEqual(ws.to_data(), _sample())

    def test_to_data_is_deep_copy(self):
        snap = self.ws.to_data()
        snap["items"].clear()
        self.assertEqual(self.ws.to_data(), _sample())

    def test_total_word_count(self):
        # n1: alpha, beta, gamma = 3; r1: 10; n2: one, two, three = 3
        self.assertEqual(self.ws.total_word_count(), 16)

    def test_unresolved_markers(self):
        self.assertEqual(self.ws.unresolved_markers(),
                         ["cite1", "todo1", "x"])

    def test_outline(self):
        self.assertEqual(self.ws.outline(), "\n".join([
            "Research",
            "  note: Intro",
            "  section: Body",
            "    reference: Paper",
            "    group: Drafts",
            "      note: Draft",
        ]))

    def test_move_append_and_insert(self):
        self.ws.move("n1", "g1")
        self.ws.move("r1", "workspace", 0)
        data = self.ws.to_data()
        self.assertEqual([i["id"] for i in data["items"]], ["r1", "s1"])
        group = data["items"][1]["items"][0]
        self.assertEqual([i["id"] for i in group["items"]], ["n2", "n1"])
        self.assertEqual(self.ws.total_word_count(), 16)

    def test_move_reorder_within_same_list(self):
        self.ws.move("n1", "workspace", 2)
        self.assertEqual([i["id"] for i in self.ws.to_data()["items"]],
                         ["s1", "n1"])

    def _assert_move_fails(self, *args):
        before = self.ws.to_data()
        with self.assertRaises(ValueError):
            self.ws.move(*args)
        self.assertEqual(self.ws.to_data(), before)

    def test_rejected_moves_leave_data_unchanged(self):
        self._assert_move_fails("missing", "workspace")
        self._assert_move_fails("n1", "missing")
        self._assert_move_fails("n1", "n2")            # not a container
        self._assert_move_fails("s1", "s1")            # into itself
        self._assert_move_fails("s1", "g1")            # into own descendant
        self._assert_move_fails("n1", "workspace", 3)  # out of range
        self._assert_move_fails("n1", "workspace", -1)
        self._assert_move_fails(3, "workspace")        # non-string id
        self._assert_move_fails("n1", None)


if __name__ == "__main__":
    unittest.main()
