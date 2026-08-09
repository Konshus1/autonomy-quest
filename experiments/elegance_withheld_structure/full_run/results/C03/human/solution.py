import copy
import re


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")


class _Item:
    def __init__(self, data, parent, registry):
        item_id = data.get("id")
        if not isinstance(item_id, str):
            raise ValueError("item ids must be strings")
        if item_id in registry:
            raise ValueError("item ids must be unique")
        if not isinstance(data.get("title"), str):
            raise ValueError("item titles must be strings")

        self.id = item_id
        self.kind = data["kind"]
        self.title = data["title"]
        self.parent = parent
        registry[item_id] = self

    def word_count(self):
        raise NotImplementedError

    def markers(self):
        raise NotImplementedError

    def outline_lines(self, level):
        return ["  " * level + f"{self.kind}: {self.title}"]

    def to_data(self):
        raise NotImplementedError


class _Composite(_Item):
    def __init__(self, data, parent, registry):
        super().__init__(data, parent, registry)
        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("container items must be a list")
        self.items = [_make_item(child, self, registry) for child in raw_items]

    def word_count(self):
        return sum(item.word_count() for item in self.items)

    def markers(self):
        result = set()
        for item in self.items:
            result.update(item.markers())
        return result

    def outline_lines(self, level):
        lines = super().outline_lines(level)
        for item in self.items:
            lines.extend(item.outline_lines(level + 1))
        return lines

    def to_data(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "items": [item.to_data() for item in self.items],
        }


class _Note(_Item):
    def __init__(self, data, parent, registry):
        super().__init__(data, parent, registry)
        if not isinstance(data.get("text"), str):
            raise ValueError("note text must be a string")
        self.text = data["text"]

    def word_count(self):
        text_without_markers = _MARKER_RE.sub("", self.text)
        return len(_WORD_RE.findall(text_without_markers))

    def markers(self):
        return set(_MARKER_RE.findall(self.text))

    def to_data(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
        }


class _Reference(_Item):
    def __init__(self, data, parent, registry):
        super().__init__(data, parent, registry)
        word_count = data.get("word_count")
        unresolved = data.get("unresolved")
        if not isinstance(data.get("label"), str):
            raise ValueError("reference labels must be strings")
        if isinstance(word_count, bool) or not isinstance(word_count, int) or word_count < 0:
            raise ValueError("reference word_count must be a nonnegative integer")
        if not isinstance(unresolved, list) or not all(
            isinstance(marker, str) for marker in unresolved
        ):
            raise ValueError("reference unresolved markers must be strings")
        self.label = data["label"]
        self.declared_word_count = word_count
        self.unresolved = list(unresolved)

    def word_count(self):
        return self.declared_word_count

    def markers(self):
        return set(self.unresolved)

    def to_data(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "label": self.label,
            "word_count": self.declared_word_count,
            "unresolved": list(self.unresolved),
        }


def _make_item(data, parent, registry):
    if not isinstance(data, dict):
        raise ValueError("items must be dictionaries")
    kind = data.get("kind")
    if kind in ("section", "group"):
        return _Composite(data, parent, registry)
    if kind == "note":
        return _Note(data, parent, registry)
    if kind == "reference":
        return _Reference(data, parent, registry)
    raise ValueError("unknown item kind")


class Workspace:
    def __init__(self, data):
        copied = copy.deepcopy(data)
        if not isinstance(copied, dict):
            raise ValueError("workspace data must be a dictionary")
        if not isinstance(copied.get("title"), str):
            raise ValueError("workspace title must be a string")
        if not isinstance(copied.get("items"), list):
            raise ValueError("workspace items must be a list")

        self.title = copied["title"]
        self._registry = {}
        self.items = [
            _make_item(item, self, self._registry) for item in copied["items"]
        ]

    def total_word_count(self):
        return sum(item.word_count() for item in self.items)

    def unresolved_markers(self):
        markers = set()
        for item in self.items:
            markers.update(item.markers())
        return sorted(markers)

    def outline(self):
        lines = [self.title]
        for item in self.items:
            lines.extend(item.outline_lines(1))
        return "\n".join(lines)

    def move(self, item_id, destination_id, index=None):
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("ids must be strings")

        item = self._registry.get(item_id)
        if item is None:
            raise ValueError("item id does not exist")

        if destination_id == "workspace":
            destination = self
        else:
            destination = self._registry.get(destination_id)
            if destination is None:
                raise ValueError("destination id does not exist")
            if not isinstance(destination, _Composite):
                raise ValueError("destination is not a container")

        destination_items = destination.items
        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(destination_items):
                raise ValueError("index is out of range")

        ancestor = destination
        while ancestor is not self:
            if ancestor is item:
                raise ValueError("move would create a containment cycle")
            ancestor = ancestor.parent

        source_items = item.parent.items
        source_index = source_items.index(item)
        source_items.pop(source_index)

        insertion_index = len(destination_items) if index is None else index
        destination_items.insert(insertion_index, item)
        item.parent = destination

    def to_data(self):
        return copy.deepcopy({
            "title": self.title,
            "items": [item.to_data() for item in self.items],
        })
