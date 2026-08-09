"""Immutable oral-history transcript editing."""

from copy import deepcopy


class ConflictError(Exception):
    """Raised when concurrent edits cannot be combined."""


class Workspace:
    FIELDS = ("id", "speaker", "start", "end", "text", "tags", "private")

    def __init__(self, segments, _notes=None, _links=None):
        copied = []
        seen = set()
        for original in segments:
            if any(field not in original for field in self.FIELDS):
                raise ValueError("segment is missing required fields")
            segment = {field: deepcopy(original[field]) for field in self.FIELDS}
            if segment["id"] in seen:
                raise ValueError("duplicate segment id: " + str(segment["id"]))
            seen.add(segment["id"])
            if not isinstance(segment["text"], str):
                raise TypeError("segment text must be a string")
            if not isinstance(segment["tags"], list):
                raise TypeError("segment tags must be a list")
            segment["private"] = self._validate_spans(
                segment["private"], len(segment["text"])
            )
            copied.append(segment)
        copied.sort(key=lambda segment: (
            segment["start"], segment["end"], segment["id"]
        ))
        self._segments = tuple(copied)
        self._notes = self._copy_attachments(_notes or {})
        self._links = self._copy_attachments(_links or {})

    @staticmethod
    def _validate_spans(spans, length):
        if not isinstance(spans, list):
            raise TypeError("private must be a list")
        result = []
        for span in spans:
            if (
                not isinstance(span, (list, tuple))
                or len(span) != 2
                or not all(isinstance(value, int) for value in span)
            ):
                raise ValueError("private spans must be integer pairs")
            start, end = span
            if start < 0 or start >= end or end > length:
                raise ValueError("private span outside segment text")
            result.append([start, end])
        return result

    @staticmethod
    def _copy_attachments(attachments):
        return {
            attachment_id: {
                "value": deepcopy(item["value"]),
                "segments": set(item["segments"]),
            }
            for attachment_id, item in attachments.items()
        }

    def _spawn(self, segments=None, notes=None, links=None):
        return Workspace(
            self._segments if segments is None else segments,
            self._notes if notes is None else notes,
            self._links if links is None else links,
        )

    def _position(self, segment_id):
        for index, segment in enumerate(self._segments):
            if segment["id"] == segment_id:
                return index
        raise KeyError(segment_id)

    def _edit_segment(self, segment_id, edit):
        index = self._position(segment_id)
        segments = deepcopy(list(self._segments))
        edit(segments[index])
        return self._spawn(segments=segments)

    def correct(self, id, text):
        if not isinstance(text, str):
            raise TypeError("text must be a string")

        def edit(segment):
            segment["text"] = text
            segment["private"] = [
                [start, min(end, len(text))]
                for start, end in segment["private"]
                if start < len(text)
            ]

        return self._edit_segment(id, edit)

    def split(self, id, offset, left_id, right_id):
        index = self._position(id)
        source = self._segments[index]
        if not isinstance(offset, int) or not 0 < offset < len(source["text"]):
            raise ValueError("split offset must be inside the text")
        existing = {segment["id"] for segment in self._segments}
        if left_id == right_id or left_id in existing or right_id in existing:
            raise ValueError("split IDs must be new and unique")

        middle = source["start"] + (
            (source["end"] - source["start"])
            * offset
            / len(source["text"])
        )
        left_private = []
        right_private = []
        for start, end in source["private"]:
            if start < offset:
                left_private.append([start, min(end, offset)])
            if end > offset:
                right_private.append([
                    max(start, offset) - offset,
                    end - offset,
                ])

        left = {
            "id": left_id,
            "speaker": source["speaker"],
            "start": source["start"],
            "end": middle,
            "text": source["text"][:offset],
            "tags": deepcopy(source["tags"]),
            "private": left_private,
        }
        right = {
            "id": right_id,
            "speaker": source["speaker"],
            "start": middle,
            "end": source["end"],
            "text": source["text"][offset:],
            "tags": deepcopy(source["tags"]),
            "private": right_private,
        }
        segments = (
            list(self._segments[:index])
            + [left, right]
            + list(self._segments[index + 1:])
        )
        notes = self._retarget(self._notes, {id}, {left_id, right_id})
        links = self._retarget(self._links, {id}, {left_id, right_id})
        return self._spawn(segments=segments, notes=notes, links=links)

    def join(self, left_id, right_id, joined_id):
        left_index = self._position(left_id)
        right_index = self._position(right_id)
        if right_index != left_index + 1:
            raise ValueError("segments must be adjacent in workspace order")

        left = self._segments[left_index]
        right = self._segments[right_index]
        if left["speaker"] != right["speaker"]:
            raise ValueError("segments must have the same speaker")
        if left["end"] != right["start"]:
            raise ValueError("segment timings must touch")
        if joined_id in {segment["id"] for segment in self._segments}:
            raise ValueError("joined ID must be new and unique")

        shift = len(left["text"]) + 1
        joined = {
            "id": joined_id,
            "speaker": left["speaker"],
            "start": left["start"],
            "end": right["end"],
            "text": left["text"] + " " + right["text"],
            "tags": list(dict.fromkeys(left["tags"] + right["tags"])),
            "private": deepcopy(left["private"]) + [
                [start + shift, end + shift]
                for start, end in right["private"]
            ],
        }
        segments = (
            list(self._segments[:left_index])
            + [joined]
            + list(self._segments[right_index + 1:])
        )
        notes = self._retarget(
            self._notes, {left_id, right_id}, {joined_id}
        )
        links = self._retarget(
            self._links, {left_id, right_id}, {joined_id}
        )
        return self._spawn(segments=segments, notes=notes, links=links)

    @classmethod
    def _retarget(cls, attachments, old_ids, new_ids):
        result = cls._copy_attachments(attachments)
        for item in result.values():
            if item["segments"] & old_ids:
                item["segments"].difference_update(old_ids)
                item["segments"].update(new_ids)
        return result

    def add_tag(self, id, tag):
        def edit(segment):
            if tag not in segment["tags"]:
                segment["tags"].append(deepcopy(tag))

        return self._edit_segment(id, edit)

    def mark_private(self, id, start, end):
        def edit(segment):
            self._validate_spans([[start, end]], len(segment["text"]))
            segment["private"].append([start, end])

        return self._edit_segment(id, edit)

    def add_note(self, note_id, segment_id, text):
        self._position(segment_id)
        notes = self._copy_attachments(self._notes)
        if note_id in notes and notes[note_id]["value"] != text:
            raise ValueError("note ID already has different text")
        notes.setdefault(note_id, {
            "value": deepcopy(text),
            "segments": set(),
        })
        notes[note_id]["segments"].add(segment_id)
        return self._spawn(notes=notes)

    def add_link(self, link_id, segment_id, url):
        self._position(segment_id)
        links = self._copy_attachments(self._links)
        if link_id in links and links[link_id]["value"] != url:
            raise ValueError("link ID already has a different URL")
        links.setdefault(link_id, {
            "value": deepcopy(url),
            "segments": set(),
        })
        links[link_id]["segments"].add(segment_id)
        return self._spawn(links=links)

    def _attachment_output(self, attachments, value_name):
        order = {
            segment["id"]: index
            for index, segment in enumerate(self._segments)
        }
        return [
            {
                "id": attachment_id,
                value_name: deepcopy(attachments[attachment_id]["value"]),
                "segments": sorted(
                    attachments[attachment_id]["segments"],
                    key=order.__getitem__,
                ),
            }
            for attachment_id in sorted(attachments)
        ]

    def notes(self):
        return self._attachment_output(self._notes, "text")

    def links(self):
        return self._attachment_output(self._links, "url")

    @staticmethod
    def _public_text(text, spans):
        merged = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        pieces = []
        cursor = 0
        for start, end in merged:
            pieces.append(text[cursor:start])
            pieces.append("[PRIVATE]")
            cursor = end
        pieces.append(text[cursor:])
        return "".join(pieces)

    def export(self, clearance):
        if clearance not in ("public", "researcher"):
            raise ValueError("clearance must be public or researcher")
        lines = []
        for segment in self._segments:
            text = segment["text"]
            if clearance == "public":
                text = self._public_text(text, segment["private"])
            lines.append(
                f'{segment["start"]}-{segment["end"]} '
                f'{segment["speaker"]}: {text}'
            )
        return "\n".join(lines)

    def speaker_index(self):
        result = {}
        for segment in self._segments:
            result.setdefault(segment["speaker"], []).append(segment["id"])
        return result

    def topic_index(self):
        result = {}
        for segment in self._segments:
            for tag in segment["tags"]:
                ids = result.setdefault(tag, [])
                if not ids or ids[-1] != segment["id"]:
                    ids.append(segment["id"])
        return result

    def compare(self, other):
        if not isinstance(other, Workspace):
            raise TypeError("other must be a Workspace")
        current = {
            segment["id"]: segment for segment in self._segments
        }
        comparison = {
            segment["id"]: segment for segment in other._segments
        }
        changes = []
        for segment_id in sorted(set(current) | set(comparison)):
            if segment_id not in current:
                changes.append("added " + str(segment_id))
            elif segment_id not in comparison:
                changes.append("removed " + str(segment_id))
            elif current[segment_id] != comparison[segment_id]:
                changes.append("changed " + str(segment_id))
        return changes


def merge(base, left, right):
    """Merge branches whose only changes are corrections and tag additions."""
    if not all(isinstance(item, Workspace) for item in (base, left, right)):
        raise TypeError("merge arguments must be Workspaces")

    base_map = {segment["id"]: segment for segment in base._segments}
    left_map = {segment["id"]: segment for segment in left._segments}
    right_map = {segment["id"]: segment for segment in right._segments}
    base_ids = set(base_map)

    if set(left_map) != base_ids or set(right_map) != base_ids:
        raise ValueError("branches contain unsupported structural edits")
    if (
        left.notes() != base.notes()
        or right.notes() != base.notes()
        or left.links() != base.links()
        or right.links() != base.links()
    ):
        raise ValueError("branches contain unsupported attachment edits")

    structural_fields = ("id", "speaker", "start", "end", "private")
    result = []
    for original in base._segments:
        segment_id = original["id"]
        left_segment = left_map[segment_id]
        right_segment = right_map[segment_id]

        for field in structural_fields:
            if (
                left_segment[field] != original[field]
                or right_segment[field] != original[field]
            ):
                raise ValueError("branches contain unsupported edits")

        for branch_segment in (left_segment, right_segment):
            if any(tag not in branch_segment["tags"] for tag in original["tags"]):
                raise ValueError("branches may only add tags")

        left_changed = left_segment["text"] != original["text"]
        right_changed = right_segment["text"] != original["text"]
        if (
            left_changed
            and right_changed
            and left_segment["text"] != right_segment["text"]
        ):
            raise ConflictError("conflict on " + str(segment_id) + ": text")

        merged = deepcopy(original)
        if left_changed:
            merged["text"] = left_segment["text"]
        elif right_changed:
            merged["text"] = right_segment["text"]
        merged["tags"] = list(dict.fromkeys(
            original["tags"]
            + left_segment["tags"]
            + right_segment["tags"]
        ))
        result.append(merged)

    return Workspace(result, base._notes, base._links)
