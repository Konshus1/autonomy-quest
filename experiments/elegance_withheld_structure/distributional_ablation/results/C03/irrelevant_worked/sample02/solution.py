import copy
import re
from typing import Iterator, Optional


__all__ = ["Workspace"]


_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = {"section", "group"}


class Workspace:
    def __init__(self, data: dict):
        self._data = copy.deepcopy(data)

    def _walk(self, items: Optional[list] = None) -> Iterator[dict]:
        if items is None:
            items = self._data["items"]

        for item in items:
            yield item
            if item["kind"] in _CONTAINER_KINDS:
                yield from self._walk(item["items"])

    def total_word_count(self) -> int:
        total = 0
        for item in self._walk():
            if item["kind"] == "note":
                text_without_markers = _MARKER_RE.sub(" ", item["text"])
                total += len(_WORD_RE.findall(text_without_markers))
            elif item["kind"] == "reference":
                total += item["word_count"]
        return total

    def unresolved_markers(self) -> list[str]:
        identifiers = set()
        for item in self._walk():
            if item["kind"] == "note":
                identifiers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                identifiers.update(item["unresolved"])
        return sorted(identifiers)

    def outline(self) -> str:
        lines = [self._data["title"]]

        def add_items(items: list, depth: int) -> None:
            for item in items:
                lines.append(
                    f"{'  ' * depth}{item['kind']}: {item['title']}"
                )
                if item["kind"] in _CONTAINER_KINDS:
                    add_items(item["items"], depth + 1)

        add_items(self._data["items"], 1)
        return "\n".join(lines)

    def _find_item(self, item_id: str):
        def search(items: list):
            for position, item in enumerate(items):
                if item["id"] == item_id:
                    return item, items, position
                if item["kind"] in _CONTAINER_KINDS:
                    found = search(item["items"])
                    if found is not None:
                        return found
            return None

        return search(self._data["items"])

    @staticmethod
    def _contains(root: dict, candidate: dict) -> bool:
        if root is candidate:
            return True
        if root["kind"] in _CONTAINER_KINDS:
            return any(
                Workspace._contains(child, candidate)
                for child in root["items"]
            )
        return False

    def move(self, item_id, destination_id, index=None) -> None:
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        source = self._find_item(item_id)
        if source is None:
            raise ValueError("item id not found")
        item, source_items, source_index = source

        if destination_id == "workspace":
            destination = None
            destination_items = self._data["items"]
        else:
            destination_result = self._find_item(destination_id)
            if destination_result is None:
                raise ValueError("destination id not found")
            destination = destination_result[0]
            if destination["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination["items"]

        if destination is not None and self._contains(item, destination):
            raise ValueError("move would create invalid containment")

        if index is not None:
            if not isinstance(index, int):
                raise ValueError("index must be an integer or None")
            if index < 0 or index > len(destination_items):
                raise ValueError("index out of range")

        moved_item = source_items.pop(source_index)
        if index is None:
            destination_items.append(moved_item)
        else:
            destination_items.insert(index, moved_item)

    def to_data(self) -> dict:
        return copy.deepcopy(self._data)
