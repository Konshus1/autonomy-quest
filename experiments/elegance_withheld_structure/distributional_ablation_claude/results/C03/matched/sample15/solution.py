import copy
import re

_MARKER = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = ("section", "group")


class Workspace:
    def __init__(self, data):
        self._data = copy.deepcopy(data)

    # ------------------------------------------------------------------ #
    # Aggregation

    def total_word_count(self):
        return self._count_words(self._data["items"])

    def _count_words(self, items):
        total = 0
        for item in items:
            kind = item["kind"]
            if kind == "note":
                # Markers and their identifiers must not count as words.
                text = _MARKER.sub(" ", item["text"])
                total += len(_WORD.findall(text))
            elif kind == "reference":
                total += item["word_count"]
            else:
                total += self._count_words(item["items"])
        return total

    def unresolved_markers(self):
        found = set()

        def walk(items):
            for item in items:
                kind = item["kind"]
                if kind == "note":
                    found.update(_MARKER.findall(item["text"]))
                elif kind == "reference":
                    found.update(item["unresolved"])
                else:
                    walk(item["items"])

        walk(self._data["items"])
        return sorted(found)

    # ------------------------------------------------------------------ #
    # Rendering

    def outline(self):
        lines = [self._data["title"]]

        def walk(items, level):
            for item in items:
                lines.append("  " * level + "%s: %s" % (item["kind"], item["title"]))
                if item["kind"] in _CONTAINER_KINDS:
                    walk(item["items"], level + 1)

        walk(self._data["items"], 1)
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Mutation

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str):
            raise ValueError("item_id must be a string")
        if not isinstance(destination_id, str):
            raise ValueError("destination_id must be a string")

        found = self._find(item_id, self._data["items"])
        if found is None:
            raise ValueError("no item with id %r" % (item_id,))
        source_items, position, item = found

        if destination_id == "workspace":
            dest_items = self._data["items"]
        else:
            dest_found = self._find(destination_id, self._data["items"])
            if dest_found is None:
                raise ValueError("no destination with id %r" % (destination_id,))
            dest = dest_found[2]
            if dest["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination %r is not a container" % (destination_id,))
            if destination_id in self._subtree_ids(item):
                raise ValueError(
                    "cannot move %r into itself or its descendants" % (item_id,)
                )
            dest_items = dest["items"]

        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("index must be an integer or None")
            if not 0 <= index <= len(dest_items):
                raise ValueError("index %r out of range" % (index,))

        # All validation passed; the mutation below cannot fail.
        del source_items[position]
        if index is None:
            dest_items.append(item)
        else:
            dest_items.insert(index, item)

    # ------------------------------------------------------------------ #
    # Export

    def to_data(self):
        return copy.deepcopy(self._data)

    # ------------------------------------------------------------------ #
    # Internal helpers

    def _find(self, item_id, items):
        for i, item in enumerate(items):
            if item["id"] == item_id:
                return items, i, item
            if item["kind"] in _CONTAINER_KINDS:
                found = self._find(item_id, item["items"])
                if found is not None:
                    return found
        return None

    def _subtree_ids(self, item):
        ids = {item["id"]}
        if item["kind"] in _CONTAINER_KINDS:
            for child in item["items"]:
                ids |= self._subtree_ids(child)
        return ids


if __name__ == "__main__":
    data = {
        "title": "Research",
        "items": [
            {
                "kind": "section",
                "id": "s1",
                "title": "Background",
                "items": [
                    {
                        "kind": "note",
                        "id": "n1",
                        "title": "Intro",
                        "text": "Two words [[?cite1]] here",
                    },
                    {
                        "kind": "reference",
                        "id": "r1",
                        "title": "Paper",
                        "label": "Smith 2020",
                        "word_count": 5,
                        "unresolved": ["cite2"],
                    },
                ],
            },
            {
                "kind": "group",
                "id": "g1",
                "title": "Drafts",
                "items": [],
            },
        ],
    }
    ws = Workspace(data)
    assert ws.total_word_count() == 3 + 5
    assert ws.unresolved_markers() == ["cite1", "cite2"]
    assert ws.outline() == (
        "Research\n"
        "  section: Background\n"
        "    note: Intro\n"
        "    reference: Paper\n"
        "  group: Drafts"
    )
    before = ws.to_data()
    try:
        ws.move("s1", "s1")
    except ValueError:
        pass
    assert ws.to_data() == before
    ws.move("n1", "g1", 0)
    assert ws.to_data()["items"][1]["items"][0]["id"] == "n1"
    print("all checks passed")
