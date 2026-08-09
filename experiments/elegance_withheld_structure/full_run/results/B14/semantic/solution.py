from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping


class Meeting:
    """Record meeting proceedings and produce deterministic meeting documents."""

    def __init__(self, agenda: dict[str, Any]):
        self._agenda = deepcopy(agenda)
        self._items: dict[str, dict[str, Any]] = {}
        self._parent: dict[str, str | None] = {}
        self._top_level: list[str] = []
        self._index_items(self._agenda.get("items", []), None)

        self._order = list(self._top_level)
        self._withdrawn: set[str] = set()
        self._events: list[dict[str, Any]] = []
        self._motions: dict[str, str] = {}
        self._next_motion = 1
        self._errors: set[str] = set()
        self._last_timestamp = self._agenda.get("start")
        self._consent_membership = self._build_consent_membership(
            self._agenda.get("consent_groups", {})
        )

    def _index_items(
        self, items: list[dict[str, Any]], parent: str | None
    ) -> None:
        for item in items:
            number = item["number"]
            self._items[number] = item
            self._parent[number] = parent
            if parent is None:
                self._top_level.append(number)
            self._index_items(item.get("subitems", []), number)

    @staticmethod
    def _build_consent_membership(consent_groups: Any) -> dict[str, str]:
        membership: dict[str, str] = {}

        if isinstance(consent_groups, Mapping):
            groups = consent_groups.items()
        elif isinstance(consent_groups, list):
            groups = []
            for group in consent_groups:
                if isinstance(group, Mapping):
                    name = group.get("name")
                    members = group.get("items", group.get("members", []))
                    groups.append((name, members))
        else:
            groups = []

        for name, members in groups:
            if isinstance(members, Mapping):
                members = members.get("items", members.get("members", []))
            if not isinstance(members, (list, tuple, set)):
                continue
            for number in members:
                membership.setdefault(str(number), name)
        return membership

    def _timestamp_errors(self, at: Any) -> list[str]:
        if not isinstance(at, datetime):
            return ["timestamps out of order"]
        if isinstance(self._last_timestamp, datetime) and at < self._last_timestamp:
            return ["timestamps out of order"]
        return []

    def _item_errors(self, item: str) -> list[str]:
        return [] if item in self._items else [f"unknown item {item}"]

    def _reject(self, errors: list[str]) -> bool:
        if not errors:
            return False
        self._errors.update(errors)
        return True

    def _append_event(self, event: dict[str, Any], timestamp: datetime) -> None:
        event["_sequence"] = len(self._events)
        self._events.append(event)
        self._last_timestamp = timestamp

    def reorder(self, order: list[str], at: datetime) -> None:
        proposed = list(order) if isinstance(order, (list, tuple)) else []
        errors = self._timestamp_errors(at)

        for number in proposed:
            if number not in self._items:
                errors.append(f"unknown item {number}")

        valid_reorder = (
            len(proposed) == len(self._top_level)
            and len(set(proposed)) == len(proposed)
            and set(proposed) == set(self._top_level)
        )
        if not valid_reorder:
            errors.append("invalid reorder")

        if self._reject(errors):
            return

        self._order = proposed
        self._append_event(
            {"type": "reorder", "order": deepcopy(proposed), "at": at}, at
        )

    def withdraw(self, item: str, at: datetime) -> None:
        errors = self._item_errors(item) + self._timestamp_errors(at)
        if item in self._withdrawn:
            errors.append(f"item {item} already withdrawn")
        if self._reject(errors):
            return

        self._withdrawn.add(item)
        self._append_event({"type": "withdraw", "item": item, "at": at}, at)

    def add_motion(self, item: str, text: str, at: datetime) -> str:
        ref = f"motion-{self._next_motion}"
        errors = self._item_errors(item) + self._timestamp_errors(at)
        if self._reject(errors):
            return ref

        self._next_motion += 1
        self._motions[ref] = item
        self._append_event(
            {"type": "motion", "item": item, "text": text, "ref": ref, "at": at},
            at,
        )
        return ref

    def record_vote(
        self,
        item: str,
        motion_ref: str,
        votes: Mapping[str, str],
        at: datetime,
    ) -> None:
        errors = self._item_errors(item) + self._timestamp_errors(at)

        if motion_ref not in self._motions:
            errors.append(f"unknown motion {motion_ref}")
        elif self._motions[motion_ref] != item:
            errors.append(f"motion {motion_ref} is not for item {item}")

        if not isinstance(votes, Mapping) or any(
            vote not in {"yes", "no", "abstain"} for vote in votes.values()
        ):
            errors.append("invalid vote")

        if self._reject(errors):
            return

        vote_copy = dict(deepcopy(votes))
        yes = sum(vote == "yes" for vote in vote_copy.values())
        no = sum(vote == "no" for vote in vote_copy.values())
        result = "passed" if yes > no else "failed" if no > yes else "tie"
        self._append_event(
            {
                "type": "vote",
                "item": item,
                "ref": motion_ref,
                "votes": vote_copy,
                "result": result,
                "at": at,
            },
            at,
        )

    def add_recess(self, start: datetime, end: datetime) -> None:
        errors = self._timestamp_errors(start)
        if not isinstance(start, datetime) or not isinstance(end, datetime) or end < start:
            errors.append("recess ends before it starts")
        if self._reject(errors):
            return

        minutes = int((end - start).total_seconds() // 60)
        self._append_event(
            {
                "type": "recess",
                "minutes": minutes,
                "at": start,
                "_end": end,
            },
            end,
        )

    @staticmethod
    def _public_item(item: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in item.items():
            if key in {"private_notes", "action"}:
                continue
            if key == "subitems":
                result[key] = [Meeting._public_item(child) for child in value]
            else:
                result[key] = deepcopy(value)
        return result

    def _chair_item(self, item: dict[str, Any]) -> dict[str, Any]:
        result = self._public_item(item)
        result["private_notes"] = deepcopy(item.get("private_notes"))
        result["consent_group"] = self._consent_membership.get(item["number"])
        result["subitems"] = [
            self._chair_item(child) for child in item.get("subitems", [])
        ]
        return result

    def _agenda_view(self, chair: bool) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in self._agenda.items():
            if key == "start":
                result[key] = value.isoformat()
            elif key == "items":
                converter = self._chair_item if chair else self._public_item
                result[key] = [converter(item) for item in value]
            else:
                result[key] = deepcopy(value)
        return result

    def _minutes(self) -> list[dict[str, Any]]:
        minutes: list[dict[str, Any]] = []
        for event in sorted(
            self._events, key=lambda value: (value["at"], value["_sequence"])
        ):
            rendered = {
                key: deepcopy(value)
                for key, value in event.items()
                if not key.startswith("_")
            }
            rendered["at"] = event["at"].isoformat()
            minutes.append(rendered)
        return minutes

    def _is_withdrawn(self, number: str) -> bool:
        current: str | None = number
        while current is not None:
            if current in self._withdrawn:
                return True
            current = self._parent[current]
        return False

    def _actions(self) -> list[dict[str, Any]]:
        actions = []
        for number, item in self._items.items():
            if not self._is_withdrawn(number) and item.get("action") is not None:
                actions.append({"item": number, "action": deepcopy(item["action"])})
        return sorted(actions, key=lambda entry: entry["item"])

    def _top_level_for(self, item: str) -> str:
        current = item
        while self._parent[current] is not None:
            current = self._parent[current]  # type: ignore[assignment]
        return current

    def _item_proceedings(self) -> list[tuple[datetime, str]]:
        proceedings = []
        for event in sorted(
            self._events, key=lambda value: (value["at"], value["_sequence"])
        ):
            item = event.get("item")
            if item in self._items:
                proceedings.append((event["at"], self._top_level_for(item)))
        return proceedings

    def _last_proceeding_time(self) -> datetime | None:
        if not self._events:
            return None
        return max(event["at"] for event in self._events)

    def _recess_overlap_minutes(self, start: datetime, end: datetime) -> int:
        intervals: list[tuple[datetime, datetime]] = []
        for event in self._events:
            if event["type"] != "recess":
                continue
            overlap_start = max(start, event["at"])
            overlap_end = min(end, event["_end"])
            if overlap_end > overlap_start:
                intervals.append((overlap_start, overlap_end))

        intervals.sort()
        merged: list[list[datetime]] = []
        for interval_start, interval_end in intervals:
            if not merged or interval_start > merged[-1][1]:
                merged.append([interval_start, interval_end])
            elif interval_end > merged[-1][1]:
                merged[-1][1] = interval_end

        seconds = sum((end_at - start_at).total_seconds() for start_at, end_at in merged)
        return int(seconds // 60)

    def _actual_minutes(self, item: str) -> int:
        proceedings = self._item_proceedings()
        first_index = next(
            (index for index, (_, number) in enumerate(proceedings) if number == item),
            None,
        )
        if first_index is None:
            return 0

        start = proceedings[first_index][0]
        end = None
        for at, number in proceedings[first_index + 1 :]:
            if number != item:
                end = at
                break
        if end is None:
            end = self._last_proceeding_time()
        if end is None or end <= start:
            return 0

        elapsed = int((end - start).total_seconds() // 60)
        return max(0, elapsed - self._recess_overlap_minutes(start, end))

    def _comparison(self) -> list[dict[str, Any]]:
        active_order = [number for number in self._order if not self._is_withdrawn(number)]
        actual_positions = {
            number: position for position, number in enumerate(active_order, start=1)
        }

        rows = []
        for planned_position, number in enumerate(self._top_level, start=1):
            withdrawn = self._is_withdrawn(number)
            actual_position = None if withdrawn else actual_positions[number]
            if withdrawn:
                status = "withdrawn"
            elif actual_position == planned_position:
                status = "as_planned"
            else:
                status = "moved"
            rows.append(
                {
                    "item": number,
                    "planned_position": planned_position,
                    "actual_position": actual_position,
                    "expected_minutes": self._items[number]["expected_minutes"],
                    "actual_minutes": self._actual_minutes(number),
                    "status": status,
                }
            )
        return rows

    def documents(self) -> dict[str, Any]:
        return {
            "public_agenda": self._agenda_view(chair=False),
            "chair_run_sheet": self._agenda_view(chair=True),
            "minutes": self._minutes(),
            "actions": self._actions(),
            "comparison": self._comparison(),
            "errors": sorted(self._errors),
        }
