"""Immutable oral-history transcript editing and export."""

from dataclasses import dataclass


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


def _freeze_segment(value):
    required = {"id", "speaker", "start", "end", "text", "tags", "private"}
    missing = required.difference(value)
    if missing:
        raise ValueError("segment missing " + ", ".join(sorted(missing)))

    text = value["text"]
    if not isinstance(text, str):
        raise TypeError("segment text must be a string")

    private = []
    for span in value["private"]:
        if len(span) != 2:
            raise ValueError("private spans must contain two offsets")
        start, end = span
        if not isinstance(start, int) or not isinstance(end, int):
            raise TypeError("private offsets must be integers")
        if start < 0 or start >= end or end > len(text):
            raise ValueError("private span is outside segment text")
        private.append((start, end))

    return _Segment(
        value["id"],
        value["speaker"],
        value["start"],
        value["end"],
        text,
        tuple(value["tags"]),
        tuple(private),
    )


def _ordered(segments):
    return tuple(sorted(segments, key=lambda segment: (
        segment.start, segment.end, segment.id
    )))


def _clip_private(spans, length):
    return tuple(
        (start, min(end, length))
        for start, end in spans
        if start < length
    )


class Workspace:
    """An immutable transcript revision."""

    def __init__(self, segments):
        frozen = tuple(_freeze_segment(segment) for segment in segments)
        ids = [segment.id for segment in frozen]
        if len(ids) != len(set(ids)):
            raise ValueError("segment IDs must be unique")
        self._segments = _ordered(frozen)
        self._notes = {}
        self._links = {}

    @classmethod
    def _from_parts(cls, segments, notes, links):
        result = object.__new__(cls)
        result._segments = _ordered(segments)
        result._notes = {
            key: (value, frozenset(targets))
            for key, (value, targets) in notes.items()
        }
        result._links = {
            key: (value, frozenset(targets))
            for key, (value, targets) in links.items()
        }
        return result

    @property
    def segments(self):
        return [segment.as_dict() for segment in self._segments]

    def _get(self, segment_id):
        for segment in self._segments:
            if segment.id == segment_id:
                return segment
        raise KeyError(segment_id)

    def _replace(self, removed, replacements, notes=None, links=None):
        remaining = [
            segment for segment in self._segments
            if segment.id not in removed
        ]
        remaining_ids = {segment.id for segment in remaining}
        replacement_ids = [segment.id for segment in replacements]
        if (len(replacement_ids) != len(set(replacement_ids))
                or remaining_ids.intersection(replacement_ids)):
            raise ValueError("segment IDs must be unique")
        return self._from_parts(
            remaining + list(replacements),
            self._notes if notes is None else notes,
            self._links if links is None else links,
        )

    @staticmethod
    def _remap(annotations, removed, replacements):
        remapped = {}
        for annotation_id, (value, targets) in annotations.items():
            targets = set(targets)
            if targets.intersection(removed):
                targets.difference_update(removed)
                targets.update(replacements)
            remapped[annotation_id] = (value, frozenset(targets))
        return remapped

    def correct(self, id, text):
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        old = self._get(id)
        replacement = _Segment(
            old.id,
            old.speaker,
            old.start,
            old.end,
            text,
            old.tags,
            _clip_private(old.private, len(text)),
        )
        return self._replace({id}, [replacement])

    def split(self, id, offset, left_id, right_id):
        old = self._get(id)
        if not isinstance(offset, int) or not 0 < offset < len(old.text):
            raise ValueError("split offset must be inside segment text")

        boundary = old.start + (old.end - old.start) * offset / len(old.text)
        left_private = []
        right_private = []
        for start, end in old.private:
            if start < offset:
                left_private.append((start, min(end, offset)))
            if end > offset:
                right_private.append((
                    max(start, offset) - offset,
                    end - offset,
                ))

        left = _Segment(
            left_id, old.speaker, old.start, boundary,
            old.text[:offset], old.tags, tuple(left_private),
        )
        right = _Segment(
            right_id, old.speaker, boundary, old.end,
            old.text[offset:], old.tags, tuple(right_private),
        )
        notes = self._remap(self._notes, {id}, {left_id, right_id})
        links = self._remap(self._links, {id}, {left_id, right_id})
        return self._replace({id}, [left, right], notes, links)

    def join(self, left_id, right_id, joined_id):
        positions = {
            segment.id: index
            for index, segment in enumerate(self._segments)
        }
        left = self._get(left_id)
        right = self._get(right_id)

        if positions[right_id] != positions[left_id] + 1:
            raise ValueError("segments must be adjacent in workspace order")
        if left.speaker != right.speaker:
            raise ValueError("segments must have the same speaker")
        if left.end != right.start:
            raise ValueError("segment timings must be contiguous")

        shift = len(left.text) + 1
        private = left.private + tuple(
            (start + shift, end + shift)
            for start, end in right.private
        )
        tags = left.tags + tuple(
            tag for tag in right.tags if tag not in left.tags
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

        removed = {left_id, right_id}
        notes = self._remap(self._notes, removed, {joined_id})
        links = self._remap(self._links, removed, {joined_id})
        return self._replace(removed, [joined], notes, links)

    def add_tag(self, id, tag):
        old = self._get(id)
        tags = old.tags if tag in old.tags else old.tags + (tag,)
        replacement = _Segment(
            old.id, old.speaker, old.start, old.end,
            old.text, tags, old.private,
        )
        return self._replace({id}, [replacement])

    def mark_private(self, id, start, end):
        old = self._get(id)
        if (not isinstance(start, int) or not isinstance(end, int)
                or start < 0 or start >= end or end > len(old.text)):
            raise ValueError("private span is outside segment text")
        replacement = _Segment(
            old.id, old.speaker, old.start, old.end,
            old.text, old.tags, old.private + ((start, end),),
        )
        return self._replace({id}, [replacement])

    def _add_annotation(self, collection, annotation_id, segment_id, value):
        self._get(segment_id)
        updated = dict(collection)
        if annotation_id in updated:
            existing, targets = updated[annotation_id]
            if existing != value:
                raise ValueError("annotation ID already has different content")
            updated[annotation_id] = (
                existing,
                frozenset(set(targets) | {segment_id}),
            )
        else:
            updated[annotation_id] = (value, frozenset({segment_id}))
        return updated

    def add_note(self, note_id, segment_id, text):
        notes = self._add_annotation(
            self._notes, note_id, segment_id, text
        )
        return self._from_parts(self._segments, notes, self._links)

    def add_link(self, link_id, segment_id, url):
        links = self._add_annotation(
            self._links, link_id, segment_id, url
        )
        return self._from_parts(self._segments, self._notes, links)

    def _annotation_output(self, collection, value_name):
        position = {
            segment.id: index
            for index, segment in enumerate(self._segments)
        }
        return [
            {
                "id": annotation_id,
                value_name: value,
                "segments": sorted(
                    targets, key=lambda target: position[target]
                ),
            }
            for annotation_id, (value, targets)
            in sorted(collection.items())
        ]

    def notes(self):
        return self._annotation_output(self._notes, "text")

    def links(self):
        return self._annotation_output(self._links, "url")

    @staticmethod
    def _redact(segment):
        if not segment.private:
            return segment.text

        merged = []
        for start, end in sorted(segment.private):
            if merged and start <= merged[-1][1]:
                merged[-1] = (
                    merged[-1][0], max(merged[-1][1], end)
                )
            else:
                merged.append((start, end))

        pieces = []
        cursor = 0
        for start, end in merged:
            pieces.append(segment.text[cursor:start])
            pieces.append("[PRIVATE]")
            cursor = end
        pieces.append(segment.text[cursor:])
        return "".join(pieces)

    def export(self, clearance):
        if clearance not in {"public", "researcher"}:
            raise ValueError("clearance must be public or researcher")

        lines = []
        for segment in self._segments:
            text = (
                segment.text
                if clearance == "researcher"
                else self._redact(segment)
            )
            lines.append(
                f"{segment.start}-{segment.end} "
                f"{segment.speaker}: {text}"
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
            for tag in segment.tags:
                ids = result.setdefault(tag, [])
                if segment.id not in ids:
                    ids.append(segment.id)
        return result

    def compare(self, other):
        if not isinstance(other, Workspace):
            raise TypeError("other must be a Workspace")

        current = {segment.id: segment for segment in self._segments}
        compared = {segment.id: segment for segment in other._segments}
        changes = []
        for segment_id in sorted(set(current) | set(compared)):
            if segment_id not in current:
                changes.append(f"added {segment_id}")
            elif segment_id not in compared:
                changes.append(f"removed {segment_id}")
            elif current[segment_id] != compared[segment_id]:
                changes.append(f"changed {segment_id}")
        return changes


def _branch_delta(base, branch):
    base_segments = {segment.id: segment for segment in base._segments}
    branch_segments = {segment.id: segment for segment in branch._segments}
    message = "merge branches may contain only corrections and tag additions"

    if set(base_segments) != set(branch_segments):
        raise ValueError(message)
    if branch._notes != base._notes or branch._links != base._links:
        raise ValueError(message)

    deltas = {}
    for segment_id, original in base_segments.items():
        edited = branch_segments[segment_id]
        if (
            edited.speaker != original.speaker
            or edited.start != original.start
            or edited.end != original.end
        ):
            raise ValueError(message)

        expected_private = (
            original.private
            if edited.text == original.text
            else _clip_private(original.private, len(edited.text))
        )
        if edited.private != expected_private:
            raise ValueError(message)

        if edited.tags[:len(original.tags)] != original.tags:
            raise ValueError(message)
        additions = edited.tags[len(original.tags):]
        if (len(additions) != len(set(additions))
                or any(tag in original.tags for tag in additions)):
            raise ValueError(message)

        text = edited.text if edited.text != original.text else None
        if text is not None or additions:
            deltas[segment_id] = (text, additions)
    return deltas


def merge(base, left, right):
    """Merge correction and tag deltas from two branches."""
    if not all(isinstance(item, Workspace) for item in (base, left, right)):
        raise TypeError("base, left, and right must be Workspaces")

    left_delta = _branch_delta(base, left)
    right_delta = _branch_delta(base, right)
    merged = []

    for original in base._segments:
        left_text, left_tags = left_delta.get(original.id, (None, ()))
        right_text, right_tags = right_delta.get(original.id, (None, ()))

        if (left_text is not None and right_text is not None
                and left_text != right_text):
            raise ConflictError(f"conflict on {original.id}: text")

        if right_text is not None:
            text = right_text
        elif left_text is not None:
            text = left_text
        else:
            text = original.text

        tags = original.tags
        for tag in left_tags + right_tags:
            if tag not in tags:
                tags += (tag,)

        merged.append(_Segment(
            original.id,
            original.speaker,
            original.start,
            original.end,
            text,
            tags,
            _clip_private(original.private, len(text)),
        ))

    return Workspace._from_parts(merged, base._notes, base._links)


__all__ = ["Workspace", "ConflictError", "merge"]
