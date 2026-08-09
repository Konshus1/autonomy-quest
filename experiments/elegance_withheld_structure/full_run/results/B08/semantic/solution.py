from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class ConflictError(Exception):
    """Raised when concurrent transcript edits cannot be combined."""


@dataclass(frozen=True)
class _Segment:
    id: str
    speaker: str
    start: object
    end: object
    text: str
    tags: tuple
    private: tuple

    @classmethod
    def from_dict(cls, value):
        required = {"id", "speaker", "start", "end", "text", "tags", "private"}
        missing = required.difference(value)
        if missing:
            raise ValueError("missing segment fields: " + ", ".join(sorted(missing)))

        text = value["text"]
        if not isinstance(text, str):
            raise TypeError("segment text must be a string")
        if not isinstance(value["tags"], list):
            raise TypeError("segment tags must be a list")
        if not isinstance(value["private"], list):
            raise TypeError("segment private spans must be a list")

        spans = []
        for span in value["private"]:
            if not isinstance(span, (list, tuple)) or len(span) != 2:
                raise ValueError("private spans must contain two offsets")
            start, end = span
            if not isinstance(start, int) or not isinstance(end, int):
                raise TypeError("private span offsets must be integers")
            if start < 0 or start >= end or end > len(text):
                raise ValueError("invalid private span")
            spans.append((start, end))

        return cls(
            value["id"],
            value["speaker"],
            value["start"],
            value["end"],
            text,
            tuple(value["tags"]),
            tuple(spans),
        )

    def as_dict(self):
        return {
            "id": self.id,
            "speaker": self.speaker,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "tags": list(self.tags),
            "private": [list(span) for span in self.private],
        }


class Workspace:
    """An immutable revision of an oral-history transcript."""

    __slots__ = ("_segments", "_notes", "_links")

    def __init__(self, segments: Iterable[dict]):
        records = tuple(_Segment.from_dict(segment) for segment in segments)
        ids = [segment.id for segment in records]
        if len(ids) != len(set(ids)):
            raise ValueError("segment IDs must be unique")
        self._segments = tuple(sorted(records, key=_segment_order))
        self._notes = {}
        self._links = {}

    @classmethod
    def _from_state(cls, segments, notes, links):
        workspace = object.__new__(cls)
        workspace._segments = tuple(sorted(segments, key=_segment_order))
        workspace._notes = {
            note_id: (text, frozenset(targets))
            for note_id, (text, targets) in notes.items()
        }
        workspace._links = {
            link_id: (url, frozenset(targets))
            for link_id, (url, targets) in links.items()
        }
        return workspace

    @property
    def segments(self):
        """Return defensive copies of the current segment dictionaries."""
        return [segment.as_dict() for segment in self._segments]

    def _position(self, segment_id):
        for index, segment in enumerate(self._segments):
            if segment.id == segment_id:
                return index
        raise KeyError(segment_id)

    def correct(self, segment_id, text):
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        index = self._position(segment_id)
        old = self._segments[index]
        private = _clip_private(old.private, len(text))
        replacement = _Segment(
            old.id, old.speaker, old.start, old.end, text, old.tags, private
        )
        segments = list(self._segments)
        segments[index] = replacement
        return self._from_state(segments, self._notes, self._links)

    def split(self, segment_id, offset, left_id, right_id):
        index = self._position(segment_id)
        original = self._segments[index]
        if not isinstance(offset, int):
            raise TypeError("split offset must be an integer")
        if not 0 < offset < len(original.text):
            raise ValueError("split offset must be inside the segment text")
        if left_id == right_id:
            raise ValueError("split IDs must be distinct")

        remaining_ids = {
            segment.id for segment in self._segments if segment.id != segment_id
        }
        if left_id in remaining_ids or right_id in remaining_ids:
            raise ValueError("segment IDs must be unique")

        split_time = original.start + (
            (original.end - original.start) * offset / len(original.text)
        )
        left_private, right_private = _split_private(original.private, offset)
        left = _Segment(
            left_id,
            original.speaker,
            original.start,
            split_time,
            original.text[:offset],
            original.tags,
            left_private,
        )
        right = _Segment(
            right_id,
            original.speaker,
            split_time,
            original.end,
            original.text[offset:],
            original.tags,
            right_private,
        )

        segments = list(self._segments)
        segments[index:index + 1] = [left, right]
        notes = _replace_target(self._notes, {segment_id}, {left_id, right_id})
        links = _replace_target(self._links, {segment_id}, {left_id, right_id})
        return self._from_state(segments, notes, links)

    def join(self, left_id, right_id, joined_id):
        left_index = self._position(left_id)
        right_index = self._position(right_id)
        if right_index != left_index + 1:
            raise ValueError("segments must be adjacent in workspace order")

        left = self._segments[left_index]
        right = self._segments[right_index]
        if left.speaker != right.speaker:
            raise ValueError("joined segments must have the same speaker")
        if left.end != right.start:
            raise ValueError("joined segments must have contiguous timing")

        remaining_ids = {
            segment.id
            for segment in self._segments
            if segment.id not in {left_id, right_id}
        }
        if joined_id in remaining_ids:
            raise ValueError("segment IDs must be unique")

        tags = tuple(dict.fromkeys(left.tags + right.tags))
        shift = len(left.text) + 1
        private = left.private + tuple(
            (start + shift, end + shift) for start, end in right.private
        )
        joined = _Segment(
            joined_id,
            left.speaker,
            left.start,
            right.end,
            left.text + " " + right.text,
            tags,
            private,
        )

        segments = list(self._segments)
        segments[left_index:right_index + 1] = [joined]
        old_targets = {left_id, right_id}
        notes = _replace_target(self._notes, old_targets, {joined_id})
        links = _replace_target(self._links, old_targets, {joined_id})
        return self._from_state(segments, notes, links)

    def add_tag(self, segment_id, tag):
        index = self._position(segment_id)
        old = self._segments[index]
        tags = old.tags if tag in old.tags else old.tags + (tag,)
        replacement = _Segment(
            old.id, old.speaker, old.start, old.end, old.text, tags, old.private
        )
        segments = list(self._segments)
        segments[index] = replacement
        return self._from_state(segments, self._notes, self._links)

    def mark_private(self, segment_id, start, end):
        index = self._position(segment_id)
        old = self._segments[index]
        if not isinstance(start, int) or not isinstance(end, int):
            raise TypeError("private span offsets must be integers")
        if start < 0 or start >= end or end > len(old.text):
            raise ValueError("invalid private span")
        replacement = _Segment(
            old.id,
            old.speaker,
            old.start,
            old.end,
            old.text,
            old.tags,
            old.private + ((start, end),),
        )
        segments = list(self._segments)
        segments[index] = replacement
        return self._from_state(segments, self._notes, self._links)

    def add_note(self, note_id, segment_id, text):
        self._position(segment_id)
        notes = dict(self._notes)
        if note_id in notes:
            existing_text, targets = notes[note_id]
            if existing_text != text:
                raise ValueError("note ID already has different text")
            notes[note_id] = (text, set(targets) | {segment_id})
        else:
            notes[note_id] = (text, {segment_id})
        return self._from_state(self._segments, notes, self._links)

    def add_link(self, link_id, segment_id, url):
        self._position(segment_id)
        links = dict(self._links)
        if link_id in links:
            existing_url, targets = links[link_id]
            if existing_url != url:
                raise ValueError("link ID already has a different URL")
            links[link_id] = (url, set(targets) | {segment_id})
        else:
            links[link_id] = (url, {segment_id})
        return self._from_state(self._segments, self._notes, links)

    def notes(self):
        order = {segment.id: index for index, segment in enumerate(self._segments)}
        return [
            {
                "id": note_id,
                "text": self._notes[note_id][0],
                "segments": sorted(self._notes[note_id][1], key=order.__getitem__),
            }
            for note_id in sorted(self._notes)
        ]

    def links(self):
        order = {segment.id: index for index, segment in enumerate(self._segments)}
        return [
            {
                "id": link_id,
                "url": self._links[link_id][0],
                "segments": sorted(self._links[link_id][1], key=order.__getitem__),
            }
            for link_id in sorted(self._links)
        ]

    def export(self, clearance):
        if clearance not in {"public", "researcher"}:
            raise ValueError("clearance must be 'public' or 'researcher'")
        lines = []
        for segment in self._segments:
            text = segment.text
            if clearance == "public":
                text = _redact(text, segment.private)
            lines.append(
                f"{segment.start}-{segment.end} {segment.speaker}: {text}"
            )
        return "\n".join(lines)

    def speaker_index(self):
        result = {}
        for segment in self._segments:
            result.setdefault(segment.speaker, []).append(segment.id)
        return result

    def topic_index(self):
        result = {}
        for segment in self._segments:
            for tag in dict.fromkeys(segment.tags):
                result.setdefault(tag, []).append(segment.id)
        return result

    def compare(self, other):
        if not isinstance(other, Workspace):
            raise TypeError("other must be a Workspace")
        own = {segment.id: segment for segment in self._segments}
        theirs = {segment.id: segment for segment in other._segments}
        changes = []
        for segment_id in sorted(set(own) | set(theirs)):
            if segment_id not in own:
                changes.append(f"added {segment_id}")
            elif segment_id not in theirs:
                changes.append(f"removed {segment_id}")
            elif own[segment_id] != theirs[segment_id]:
                changes.append(f"changed {segment_id}")
        return changes


def merge(base, left, right):
    """Combine compatible correction and tag-only branches."""
    if not all(isinstance(item, Workspace) for item in (base, left, right)):
        raise TypeError("base, left, and right must be Workspace instances")

    if left._notes != base._notes or right._notes != base._notes:
        raise ValueError("merge supports only correct and add_tag edits")
    if left._links != base._links or right._links != base._links:
        raise ValueError("merge supports only correct and add_tag edits")

    base_by_id = {segment.id: segment for segment in base._segments}
    left_by_id = {segment.id: segment for segment in left._segments}
    right_by_id = {segment.id: segment for segment in right._segments}
    if set(left_by_id) != set(base_by_id) or set(right_by_id) != set(base_by_id):
        raise ValueError("merge supports only correct and add_tag edits")

    merged_segments = []
    for segment_id in base_by_id:
        original = base_by_id[segment_id]
        left_segment = left_by_id[segment_id]
        right_segment = right_by_id[segment_id]
        left_additions = _validate_merge_edit(original, left_segment)
        right_additions = _validate_merge_edit(original, right_segment)

        left_changed_text = left_segment.text != original.text
        right_changed_text = right_segment.text != original.text
        if (
            left_changed_text
            and right_changed_text
            and left_segment.text != right_segment.text
        ):
            raise ConflictError(f"conflict on {segment_id}: text")

        if left_changed_text:
            text = left_segment.text
        elif right_changed_text:
            text = right_segment.text
        else:
            text = original.text

        tags = tuple(
            dict.fromkeys(original.tags + left_additions + right_additions)
        )
        private = _clip_private(original.private, len(text))
        merged_segments.append(
            _Segment(
                original.id,
                original.speaker,
                original.start,
                original.end,
                text,
                tags,
                private,
            )
        )

    return Workspace._from_state(
        merged_segments, base._notes, base._links
    )


def _segment_order(segment):
    return segment.start, segment.end, segment.id


def _clip_private(spans, text_length):
    clipped = []
    for start, end in spans:
        new_start = min(start, text_length)
        new_end = min(end, text_length)
        if new_start < new_end:
            clipped.append((new_start, new_end))
    return tuple(clipped)


def _split_private(spans, offset):
    left = []
    right = []
    for start, end in spans:
        left_start = start
        left_end = min(end, offset)
        if left_start < left_end:
            left.append((left_start, left_end))

        right_start = max(start, offset) - offset
        right_end = end - offset
        if right_start < right_end:
            right.append((right_start, right_end))
    return tuple(left), tuple(right)


def _replace_target(records, old_targets, new_targets):
    replaced = {}
    for record_id, (value, targets) in records.items():
        targets = set(targets)
        if targets.intersection(old_targets):
            targets.difference_update(old_targets)
            targets.update(new_targets)
        replaced[record_id] = (value, targets)
    return replaced


def _maximal_spans(spans, text_length):
    clipped = sorted(_clip_private(spans, text_length))
    merged = []
    for start, end in clipped:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _redact(text, spans):
    maximal = _maximal_spans(spans, len(text))
    if not maximal:
        return text
    pieces = []
    position = 0
    for start, end in maximal:
        pieces.append(text[position:start])
        pieces.append("[PRIVATE]")
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


def _validate_merge_edit(original, candidate):
    if (
        candidate.id != original.id
        or candidate.speaker != original.speaker
        or candidate.start != original.start
        or candidate.end != original.end
    ):
        raise ValueError("merge supports only correct and add_tag edits")

    if candidate.tags[:len(original.tags)] != original.tags:
        raise ValueError("merge supports only tag additions")
    additions = candidate.tags[len(original.tags):]
    accumulated = list(original.tags)
    for tag in additions:
        if tag in accumulated:
            raise ValueError("merge supports only add_tag edits")
        accumulated.append(tag)

    expected_private = _clip_private(original.private, len(candidate.text))
    if candidate.private != expected_private:
        raise ValueError("merge supports only correct and add_tag edits")
    return additions


__all__ = ["Workspace", "ConflictError", "merge"]
