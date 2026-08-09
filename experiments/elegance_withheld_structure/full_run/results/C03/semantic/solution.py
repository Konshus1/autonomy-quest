from __future__ import annotations

import copy
import operator
import re
from typing import Any, Iterator

__all__ = ["Workspace"]

_MARKER_RE = re.compile(r"\[\[\?([^\[\]]+)\]\]")
_WORD_RE = re.compile(r"\b\w+\b")
_CONTAINER_KINDS = frozenset({"section", "group"})


class Workspace:
    def __init__(self, data: dict[str, Any]):
        self._data = copy.deepcopy(data)

    def _walk(self, items: list[dict[str, Any]] | None = None) -> Iterator[dict[str, Any]]:
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
        markers: set[str] = set()
        for item in self._walk():
            if item["kind"] == "note":
                markers.update(_MARKER_RE.findall(item["text"]))
            elif item["kind"] == "reference":
                markers.update(item["unresolved"])
        return sorted(markers)

    def outline(self) -> str:
        lines = [self._data["title"]]

        def append_items(items: list[dict[str, Any]], level: int) -> None:
            for item in items:
                lines.append(f"{'  ' * level}{item['kind']}: {item['title']}")
                if item["kind"] in _CONTAINER_KINDS:
                    append_items(item["items"], level + 1)

        append_items(self._data["items"], 1)
        return "\n".join(lines)

    def _find_location(
        self,
        item_id: str,
        items: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], int] | None:
        if items is None:
            items = self._data["items"]
        for index, item in enumerate(items):
            if item["id"] == item_id:
                return item, items, index
            if item["kind"] in _CONTAINER_KINDS:
                found = self._find_location(item_id, item["items"])
                if found is not None:
                    return found
        return None

    @staticmethod
    def _contains(root: dict[str, Any], target: dict[str, Any]) -> bool:
        if root is target:
            return True
        if root["kind"] in _CONTAINER_KINDS:
            return any(Workspace._contains(child, target) for child in root["items"])
        return False

    def move(self, item_id: str, destination_id: str, index: Any = None) -> None:
        if not isinstance(item_id, str) or not isinstance(destination_id, str):
            raise ValueError("item and destination ids must be strings")

        source = self._find_location(item_id)
        if source is None:
            raise ValueError("item id not found")
        item, source_items, source_index = source

        if destination_id == "workspace":
            destination_item = None
            destination_items = self._data["items"]
        else:
            destination = self._find_location(destination_id)
            if destination is None:
                raise ValueError("destination id not found")
            destination_item = destination[0]
            if destination_item["kind"] not in _CONTAINER_KINDS:
                raise ValueError("destination is not a container")
            destination_items = destination_item["items"]

        if destination_item is not None and self._contains(item, destination_item):
            raise ValueError("move would create invalid containment")

        if index is None:
            insertion_index = len(destination_items)
        else:
            try:
                insertion_index = operator.index(index)
            except TypeError as exc:
                raise ValueError("index must be an integer") from exc
            if insertion_index < 0 or insertion_index > len(destination_items):
                raise ValueError("index out of range")

        moved_item = source_items.pop(source_index)
        destination_items.insert(insertion_index, moved_item)

    def to_data(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)
