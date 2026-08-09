"""Immutable oral-history transcript editing and export."""

from copy import deepcopy


class ConflictError(Exception):
    """Raised when concurrent branches make incompatible edits."""


def _ordered(segments):
    return sorted(
        segments,
        key=lambda segment: (segment["start"], segment["end"], segment["id"]),
    )


def _validate_private(spans, text_length):
    result = []
    for span in spans:
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            raise ValueError("private spans must be [start, end] pairs")
        start, end = span
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or start >= end
            or end > text_length
        ):
            raise ValueError("invalid private span")
        result.append([start, end])
    return result


class Workspace:
    def __init__(self, segments, _notes=None, _links=None):
        copied = deepcopy(list(segments))
        required = {"id", "speaker", "start", "end", "text", "tags", "private"}
        seen = set()

        for segment in copied:
            if not required.issubset(segment):
                missing = sorted(required - set(segment))
                raise ValueError("missing segment fields: " + ", ".join(missing))
            if segment["id"] in seen:
                raise ValueError("duplicate segment id: " + str(segment["id"]))
            seen.add(segment["id"])

            if segment["end"] < segment["start"]:
                raise ValueError("segment end precedes start")
            if not isinstance(segment["text"], str):
                raise ValueError("segment text must be a string")
            if not isinstance(segment["tags"], list):
                raise ValueError("segment tags must be a list")
            if not isinstance(segment["private"], list):
                raise ValueError("segment private data must be a list")

            segment["tags"] = deepcopy(segment["tags"])
            segment["private"] = _validate_private(
                segment["private"], len(segment["text"])
            )

        self._segments = _ordered(copied)
        self._notes = deepcopy(_notes or {})
        self._links = deepcopy(_links or {})

    def _find(self, segment_id):
        for index, segment in enumerate(self._segments):
            if segment["id"] == segment_id:
                return index, segment
        raise KeyError(segment_id)

    def _spawn(self, segments=None, notes=None, links=None):
        return Workspace(
            self._segments if segments is None else segments,
            self._notes if notes is None else notes,
            self._links if links is None else links,
        )

    @staticmethod
    def _retarget(records, removed_ids, replacement_ids):
        result = deepcopy(records)
        for record in result.values():
            updated = []
            for segment_id in record["segments"]:
                additions = (
                    replacement_ids if segment_id in removed_ids else [segment_id]
                )
                for addition in additions:
                    if addition not in updated:
                        updated.append(addition)
            record["segments"] = updated
        return result

    def correct(self, segment_id, text):
        if not isinstance(text, str):
            raise ValueError("text must be a string")

        index, old = self._find(segment_id)
        if any(end > len(text) for _, end in old["private"]):
            raise ValueError("corrected text would invalidate a private span")

        segments = deepcopy(self._segments)
        segments[index]["text"] = text
        return self._spawn(segments=segments)

    def split(self, segment_id, offset, left_id, right_id):
        index, old = self._find(segment_id)
        if not isinstance(offset, int) or not 0 < offset < len(old["text"]):
            raise ValueError("split offset must be inside the text")

        remaining_ids = {
            segment["id"] for segment in self._segments if segment["id"] != segment_id
        }
        if left_id == right_id or left_id in remaining_ids or right_id in remaining_ids:
            raise ValueError("split IDs must be unique")

        duration = old["end"] - old["start"]
        midpoint = old["start"] + duration * offset / len(old["text"])
        left_private = []
        right_private = []

        for start, end in old["private"]:
            if start < offset:
                left_private.append([start, min(end, offset)])
            if end > offset:
                right_private.append(
                    [max(start, offset) - offset, end - offset]
                )

        left = {
            "id": left_id,
            "speaker": old["speaker"],
            "start": old["start"],
            "end": midpoint,
            "text": old["text"][:offset],
            "tags": deepcopy(old["tags"]),
            "private": left_private,
        }
        right = {
            "id": right_id,
            "speaker": old["speaker"],
            "start": midpoint,
            "end": old["end"],
            "text": old["text"][offset:],
            "tags": deepcopy(old["tags"]),
            "private": right_private,
        }

        segments = deepcopy(self._segments)
        segments[index:index + 1] = [left, right]
        notes = self._retarget(
            self._notes, {segment_id}, [left_id, right_id]
        )
        links = self._retarget(
            self._links, {segment_id}, [left_id, right_id]
        )
        return self._spawn(segments=segments, notes=notes, links=links)

    def join(self, left_id, right_id, joined_id):
        left_index, left = self._find(left_id)
        right_index, right = self._find(right_id)

        if right_index != left_index + 1:
            raise ValueError("segments must be adjacent in workspace order")
        if left["speaker"] != right["speaker"]:
            raise ValueError("segments must have the same speaker")
        if left["end"] != right["start"]:
            raise ValueError("segment timings must meet")

        remaining_ids = {
            segment["id"]
            for segment in self._segments
            if segment["id"] not in {left_id, right_id}
        }
        if joined_id in remaining_ids:
            raise ValueError("joined ID must be unique")

        tags = deepcopy(left["tags"])
        for tag in right["tags"]:
            if tag not in tags:
                tags.append(deepcopy(tag))

        shift = len(left["text"]) + 1
        joined = {
            "id": joined_id,
            "speaker": left["speaker"],
            "start": left["start"],
            "end": right["end"],
            "text": left["text"] + " " + right["text"],
            "tags": tags,
            "private": deepcopy(left["private"])
            + [
                [start + shift, end + shift]
                for start, end in right["private"]
            ],
        }

        segments = deepcopy(self._segments)
        segments[left_index:right_index + 1] = [joined]
        notes = self._retarget(
            self._notes, {left_id, right_id}, [joined_id]
        )
        links = self._retarget(
            self._links, {left_id, right_id}, [joined_id]
        )
        return self._spawn(segments=segments, notes=notes, links=links)

    def add_tag(self, segment_id, tag):
        index, old = self._find(segment_id)
        segments = deepcopy(self._segments)
        if tag not in old["tags"]:
            segments[index]["tags"].append(deepcopy(tag))
        return self._spawn(segments=segments)

    def mark_private(self, segment_id, start, end):
        index, old = self._find(segment_id)
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or start >= end
            or end > len(old["text"])
        ):
            raise ValueError("invalid private span")

        segments = deepcopy(self._segments)
        segments[index]["private"].append([start, end])
        return self._spawn(segments=segments)

    def add_note(self, note_id, segment_id, text):
        self._find(segment_id)
        notes = deepcopy(self._notes)

        if note_id in notes:
            if notes[note_id]["text"] != text:
                raise ValueError("note ID already has different text")
            if segment_id not in notes[note_id]["segments"]:
                notes[note_id]["segments"].append(segment_id)
        else:
            notes[note_id] = {
                "id": note_id,
                "text": text,
                "segments": [segment_id],
            }

        return self._spawn(notes=notes)

    def add_link(self, link_id, segment_id, url):
        self._find(segment_id)
        links = deepcopy(self._links)

        if link_id in links:
            if links[link_id]["url"] != url:
                raise ValueError("link ID already has different URL")
            if segment_id not in links[link_id]["segments"]:
                links[link_id]["segments"].append(segment_id)
        else:
            links[link_id] = {
                "id": link_id,
                "url": url,
                "segments": [segment_id],
            }

        return self._spawn(links=links)

    def _current_order(self, segment_ids):
        wanted = set(segment_ids)
        return [
            segment["id"]
            for segment in self._segments
            if segment["id"] in wanted
        ]

    def notes(self):
        result = deepcopy(list(self._notes.values()))
        for note in result:
            note["segments"] = self._current_order(note["segments"])
        return sorted(result, key=lambda note: note["id"])

    def links(self):
        result = deepcopy(list(self._links.values()))
        for link in result:
            link["segments"] = self._current_order(link["segments"])
        return sorted(result, key=lambda link: link["id"])

    @staticmethod
    def _public_text(segment):
        merged = []
        for start, end in sorted(segment["private"]):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        pieces = []
        cursor = 0
        for start, end in merged:
            pieces.append(segment["text"][cursor:start])
            pieces.append("[PRIVATE]")
            cursor = end
        pieces.append(segment["text"][cursor:])
        return "".join(pieces)

    def export(self, clearance):
        if clearance not in {"public", "researcher"}:
            raise ValueError("clearance must be public or researcher")

        lines = []
        for segment in self._segments:
            text = (
                segment["text"]
                if clearance == "researcher"
                else self._public_text(segment)
            )
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
                if segment["id"] not in ids:
                    ids.append(segment["id"])
        return result

    def compare(self, other):
        current = {
            segment["id"]: segment for segment in self._segments
        }
        compared = {
            segment["id"]: segment for segment in other._segments
        }
        changes = []

        for segment_id in sorted(set(current) | set(compared)):
            if segment_id not in current:
                changes.append("added " + str(segment_id))
            elif segment_id not in compared:
                changes.append("removed " + str(segment_id))
            elif current[segment_id] != compared[segment_id]:
                changes.append("changed " + str(segment_id))

        return changes


def merge(base, left, right):
    """Merge branches containing only corrections and tag additions."""
    base_map = {segment["id"]: segment for segment in base._segments}
    left_map = {segment["id"]: segment for segment in left._segments}
    right_map = {segment["id"]: segment for segment in right._segments}

    if set(base_map) != set(left_map) or set(base_map) != set(right_map):
        raise ValueError("merge supports only correct and add_tag edits")
    if left._notes != base._notes or right._notes != base._notes:
        raise ValueError("merge supports only correct and add_tag edits")
    if left._links != base._links or right._links != base._links:
        raise ValueError("merge supports only correct and add_tag edits")

    result = deepcopy(base._segments)
    result_map = {segment["id"]: segment for segment in result}
    protected = ("id", "speaker", "start", "end", "private")

    for segment_id in sorted(base_map):
        original = base_map[segment_id]
        left_segment = left_map[segment_id]
        right_segment = right_map[segment_id]

        if any(
            left_segment[field] != original[field]
            or right_segment[field] != original[field]
            for field in protected
        ):
            raise ValueError("merge supports only correct and add_tag edits")

        base_tags = original["tags"]
        if any(tag not in left_segment["tags"] for tag in base_tags):
            raise ValueError("merge supports only tag additions")
        if any(tag not in right_segment["tags"] for tag in base_tags):
            raise ValueError("merge supports only tag additions")

        left_changed = left_segment["text"] != original["text"]
        right_changed = right_segment["text"] != original["text"]

        if (
            left_changed
            and right_changed
            and left_segment["text"] != right_segment["text"]
        ):
            raise ConflictError(
                "conflict on " + str(segment_id) + ": text"
            )

        target = result_map[segment_id]
        if left_changed:
            target["text"] = left_segment["text"]
        elif right_changed:
            target["text"] = right_segment["text"]

        tags = deepcopy(base_tags)
        for branch_tags in (left_segment["tags"], right_segment["tags"]):
            for tag in branch_tags:
                if tag not in tags:
                    tags.append(deepcopy(tag))
        target["tags"] = tags

    return Workspace(result, base._notes, base._links)
