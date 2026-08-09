"""Meeting agenda planning, occurrence recording, and document projections."""

from copy import deepcopy
from datetime import datetime


class Meeting:
    """Preserve an agenda plan and record valid meeting occurrences."""

    def __init__(self, agenda):
        self._agenda = deepcopy(agenda)
        self._items = {}
        self._parents = {}
        self._top_numbers = []
        self._index_items(self._agenda.get("items", []), None)

        self._order = list(self._top_numbers)
        self._withdrawn = set()
        self._events = []
        self._errors = []
        self._last_at = None
        self._motions = {}
        self._next_motion = 1
        self._recesses = []
        self._consent_membership = self._build_consent_membership(
            self._agenda.get("consent_groups", {})
        )

    def _index_items(self, items, parent):
        for item in items:
            number = item["number"]
            self._items[number] = item
            self._parents[number] = parent
            if parent is None:
                self._top_numbers.append(number)
            self._index_items(item.get("subitems", []), number)

    @staticmethod
    def _build_consent_membership(groups):
        membership = {}
        if isinstance(groups, dict):
            entries = list(groups.items())
        else:
            entries = []
            for group in groups:
                if isinstance(group, dict):
                    entries.append(
                        (
                            group.get("name"),
                            group.get("items", group.get("members", [])),
                        )
                    )

        for name, members in entries:
            if isinstance(members, dict):
                members = members.get("items", members.get("members", []))
            for number in members or []:
                membership[number] = name
        return membership

    @classmethod
    def _render(cls, value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: cls._render(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._render(item) for item in value]
        return deepcopy(value)

    def _item_time_errors(self, item, at):
        errors = []
        if item not in self._items:
            errors.append("unknown item " + str(item))
        if self._last_at is not None and at < self._last_at:
            errors.append("timestamps out of order")
        return errors

    def _accept(self, event, errors, effective_at=None):
        if errors:
            self._errors.extend(dict.fromkeys(errors))
            return False
        self._events.append(event)
        self._last_at = effective_at if effective_at is not None else event["at"]
        return True

    def reorder(self, order, at):
        requested = list(order)
        errors = []

        for number in requested:
            if number not in self._items:
                errors.append("unknown item " + str(number))

        if self._last_at is not None and at < self._last_at:
            errors.append("timestamps out of order")

        if (
            len(requested) != len(self._top_numbers)
            or len(set(requested)) != len(requested)
            or set(requested) != set(self._top_numbers)
        ):
            errors.append("invalid reorder")

        event = {"type": "reorder", "order": requested, "at": at}
        if self._accept(event, errors):
            self._order = requested

    def withdraw(self, item, at):
        errors = self._item_time_errors(item, at)
        event = {"type": "withdraw", "item": item, "at": at}
        if self._accept(event, errors):
            self._withdrawn.add(item)

    def add_motion(self, item, text, at):
        errors = self._item_time_errors(item, at)
        if errors:
            self._errors.extend(dict.fromkeys(errors))
            return None

        ref = "M" + str(self._next_motion)
        event = {
            "type": "motion",
            "item": item,
            "text": text,
            "ref": ref,
            "at": at,
        }
        self._accept(event, [])
        self._motions[ref] = item
        self._next_motion += 1
        return ref

    def record_vote(self, item, motion_ref, votes, at):
        errors = self._item_time_errors(item, at)
        recorded_votes = dict(votes)
        yes = sum(value == "yes" for value in recorded_votes.values())
        no = sum(value == "no" for value in recorded_votes.values())

        if yes > no:
            result = "passed"
        elif no > yes:
            result = "failed"
        else:
            result = "tie"

        event = {
            "type": "vote",
            "item": item,
            "ref": motion_ref,
            "votes": recorded_votes,
            "result": result,
            "at": at,
        }
        self._accept(event, errors)

    def add_recess(self, start, end):
        errors = []
        if end < start:
            errors.append("recess ends before it starts")
        if self._last_at is not None and start < self._last_at:
            errors.append("timestamps out of order")

        minutes = int((end - start).total_seconds() // 60)
        event = {"type": "recess", "minutes": minutes, "at": start}
        if self._accept(event, errors, effective_at=end):
            self._recesses.append((start, end))

    def _render_item(self, item, chair):
        rendered = {}
        for key, value in item.items():
            if key in {"private_notes", "action", "subitems"}:
                continue
            rendered[key] = self._render(value)

        rendered["subitems"] = [
            self._render_item(child, chair) for child in item.get("subitems", [])
        ]

        if chair:
            rendered["private_notes"] = deepcopy(item.get("private_notes"))
            rendered["consent_group"] = deepcopy(
                self._consent_membership.get(item["number"])
            )
        return rendered

    def _agenda_view(self, chair):
        rendered = {}
        for key, value in self._agenda.items():
            if key == "items":
                rendered[key] = [self._render_item(item, chair) for item in value]
            else:
                rendered[key] = self._render(value)
        return rendered

    def _minutes(self):
        return [self._render(event) for event in self._events]

    def _effectively_withdrawn(self, number):
        current = number
        while current is not None:
            if current in self._withdrawn:
                return True
            current = self._parents.get(current)
        return False

    def _actions(self):
        actions = []
        for number, item in self._items.items():
            action = item.get("action")
            if action is not None and not self._effectively_withdrawn(number):
                actions.append({"item": number, "action": deepcopy(action)})
        return sorted(actions, key=lambda entry: entry["item"])

    def _actual_minutes(self, item):
        first_index = None
        for index, event in enumerate(self._events):
            if event.get("item") == item:
                first_index = index
                break

        if first_index is None:
            return 0

        start = self._events[first_index]["at"]
        end = self._events[-1]["at"]

        for event in self._events[first_index + 1 :]:
            other_item = event.get("item")
            if other_item is not None and other_item != item:
                end = event["at"]
                break

        elapsed = max(0, int((end - start).total_seconds() // 60))
        recess_minutes = 0
        for recess_start, recess_end in self._recesses:
            overlap_start = max(start, recess_start)
            overlap_end = min(end, recess_end)
            if overlap_end > overlap_start:
                recess_minutes += int(
                    (overlap_end - overlap_start).total_seconds() // 60
                )
        return max(0, elapsed - recess_minutes)

    def _comparison(self):
        live_order = [
            number for number in self._order if number not in self._withdrawn
        ]
        positions = {
            number: position
            for position, number in enumerate(live_order, start=1)
        }
        comparison = []

        for planned_position, number in enumerate(self._top_numbers, start=1):
            if number in self._withdrawn:
                actual_position = None
                status = "withdrawn"
            else:
                actual_position = positions[number]
                status = (
                    "as_planned"
                    if actual_position == planned_position
                    else "moved"
                )

            comparison.append(
                {
                    "item": number,
                    "planned_position": planned_position,
                    "actual_position": actual_position,
                    "expected_minutes": self._items[number].get(
                        "expected_minutes"
                    ),
                    "actual_minutes": self._actual_minutes(number),
                    "status": status,
                }
            )
        return comparison

    def documents(self):
        """Return fresh deterministic projections without changing state."""
        return {
            "public_agenda": self._agenda_view(False),
            "chair_run_sheet": self._agenda_view(True),
            "minutes": self._minutes(),
            "actions": self._actions(),
            "comparison": self._comparison(),
            "errors": sorted(self._errors),
        }
