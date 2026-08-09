from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


class Meeting:
    def __init__(self, agenda: Dict[str, Any]):
        self._agenda = deepcopy(agenda)
        self._items: Dict[str, Dict[str, Any]] = {}
        self._top_for: Dict[str, str] = {}
        self._children: Dict[str, Set[str]] = {}
        self._top_level: List[str] = []
        self._index_items(self._agenda.get("items", []))

        self._consent_for = self._index_consent_groups(
            self._agenda.get("consent_groups", {})
        )
        self._order = list(self._top_level)
        self._withdrawn: Set[str] = set()
        self._events: List[Dict[str, Any]] = []
        self._recesses: List[Tuple[datetime, datetime]] = []
        self._errors: List[str] = []
        self._last_timestamp: Optional[datetime] = None
        self._motion_counter = 0
        self._motions: Dict[str, str] = {}

    def _index_items(self, items: Iterable[Dict[str, Any]]) -> None:
        def visit(item: Dict[str, Any], top: str) -> Set[str]:
            number = item["number"]
            self._items[number] = item
            self._top_for[number] = top
            descendants: Set[str] = set()
            for child in item.get("subitems", []):
                child_number = child["number"]
                descendants.add(child_number)
                descendants.update(visit(child, top))
            self._children[number] = descendants
            return descendants

        for item in items:
            number = item["number"]
            self._top_level.append(number)
            visit(item, number)

    @staticmethod
    def _index_consent_groups(groups: Any) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if isinstance(groups, dict):
            for name, members in groups.items():
                if isinstance(members, dict):
                    members = members.get("items", members.get("members", []))
                if isinstance(members, (list, tuple, set)):
                    for member in members:
                        result[str(member)] = name
        elif isinstance(groups, list):
            for group in groups:
                if not isinstance(group, dict):
                    continue
                name = group.get("name", group.get("group"))
                members = group.get("items", group.get("members", []))
                for member in members:
                    result[str(member)] = name
        return result

    @staticmethod
    def _iso(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: Meeting._iso(item) for key, item in value.items()}
        if isinstance(value, list):
            return [Meeting._iso(item) for item in value]
        if isinstance(value, tuple):
            return [Meeting._iso(item) for item in value]
        return deepcopy(value)

    def _timestamp_errors(self, at: datetime) -> List[str]:
        if self._last_timestamp is not None and at < self._last_timestamp:
            return ["timestamps out of order"]
        return []

    def _unknown_item_errors(self, item: str) -> List[str]:
        if item not in self._items:
            return [f"unknown item {item}"]
        return []

    def _reject_or_record_errors(self, errors: Iterable[str]) -> bool:
        errors = list(errors)
        if not errors:
            return False
        self._errors.extend(errors)
        return True

    def reorder(self, order: List[str], at: datetime) -> None:
        proposed = deepcopy(list(order))
        errors = self._timestamp_errors(at)
        for item in proposed:
            if item not in self._items:
                errors.append(f"unknown item {item}")

        active = [item for item in self._order if item not in self._withdrawn]
        valid = (
            len(proposed) == len(active)
            and len(set(proposed)) == len(proposed)
            and set(proposed) == set(active)
            and all(item in self._top_level for item in proposed)
        )
        if not valid:
            errors.append("invalid reorder")

        if self._reject_or_record_errors(errors):
            return

        self._order = proposed
        self._events.append({"type": "reorder", "order": proposed, "at": at})
        self._last_timestamp = at

    def withdraw(self, item: str, at: datetime) -> None:
        errors = self._unknown_item_errors(item) + self._timestamp_errors(at)
        if item in self._items and item in self._withdrawn:
            errors.append(f"item {item} already withdrawn")
        if self._reject_or_record_errors(errors):
            return

        self._withdrawn.add(item)
        self._withdrawn.update(self._children.get(item, set()))
        self._events.append({"type": "withdraw", "item": item, "at": at})
        self._last_timestamp = at

    def add_motion(self, item: str, text: str, at: datetime) -> Optional[str]:
        errors = self._unknown_item_errors(item) + self._timestamp_errors(at)
        if item in self._withdrawn:
            errors.append(f"item {item} is withdrawn")
        if self._reject_or_record_errors(errors):
            return None

        self._motion_counter += 1
        ref = f"M{self._motion_counter}"
        self._motions[ref] = item
        self._events.append(
            {"type": "motion", "item": item, "text": text, "ref": ref, "at": at}
        )
        self._last_timestamp = at
        return ref

    def record_vote(
        self,
        item: str,
        motion_ref: str,
        votes: Dict[str, str],
        at: datetime,
    ) -> None:
        recorded_votes = deepcopy(votes)
        errors = self._unknown_item_errors(item) + self._timestamp_errors(at)
        if item in self._withdrawn:
            errors.append(f"item {item} is withdrawn")
        if motion_ref not in self._motions:
            errors.append(f"unknown motion {motion_ref}")
        elif self._motions[motion_ref] != item:
            errors.append(f"motion {motion_ref} does not belong to item {item}")
        if any(vote not in {"yes", "no", "abstain"} for vote in recorded_votes.values()):
            errors.append("invalid vote")
        if self._reject_or_record_errors(errors):
            return

        yes = sum(vote == "yes" for vote in recorded_votes.values())
        no = sum(vote == "no" for vote in recorded_votes.values())
        if yes > no:
            result = "passed"
        elif no > yes:
            result = "failed"
        else:
            result = "tie"

        self._events.append(
            {
                "type": "vote",
                "item": item,
                "ref": motion_ref,
                "votes": recorded_votes,
                "result": result,
                "at": at,
            }
        )
        self._last_timestamp = at

    def add_recess(self, start: datetime, end: datetime) -> None:
        errors = self._timestamp_errors(start)
        if end < start:
            errors.append("recess ends before it starts")
        if self._reject_or_record_errors(errors):
            return

        minutes = int((end - start).total_seconds() / 60)
        self._recesses.append((start, end))
        self._events.append({"type": "recess", "minutes": minutes, "at": start})
        self._last_timestamp = end

    def _agenda_view(self, chair: bool) -> Dict[str, Any]:
        def convert_item(item: Dict[str, Any]) -> Dict[str, Any]:
            converted: Dict[str, Any] = {}
            for key, value in item.items():
                if key in {"private_notes", "action"}:
                    continue
                if key == "subitems":
                    converted[key] = [convert_item(child) for child in value]
                else:
                    converted[key] = self._iso(value)
            if chair:
                converted["private_notes"] = self._iso(item.get("private_notes"))
                converted["consent_group"] = self._iso(
                    self._consent_for.get(str(item["number"]))
                )
            return converted

        result: Dict[str, Any] = {}
        for key, value in self._agenda.items():
            if key == "items":
                result[key] = [convert_item(item) for item in value]
            else:
                result[key] = self._iso(value)
        return result

    def _minutes_view(self) -> List[Dict[str, Any]]:
        return [self._iso(event) for event in self._events]

    def _actions_view(self) -> List[Dict[str, Any]]:
        actions = []
        for number, item in self._items.items():
            action = item.get("action")
            if action is not None and number not in self._withdrawn:
                actions.append({"item": number, "action": self._iso(action)})
        return sorted(actions, key=lambda entry: entry["item"])

    def _actual_minutes(self) -> Dict[str, int]:
        proceedings: List[Tuple[datetime, str]] = []
        for event in self._events:
            if event["type"] in {"motion", "vote"}:
                proceedings.append((event["at"], self._top_for[event["item"]]))

        result = {number: 0 for number in self._top_level}
        if not proceedings:
            return result

        last_proceeding = proceedings[-1][0]
        for top_number in self._top_level:
            item_times = [at for at, top in proceedings if top == top_number]
            if not item_times:
                continue
            interval_start = item_times[0]
            interval_end = last_proceeding
            for at, other_top in proceedings:
                if at > interval_start and other_top != top_number:
                    interval_end = at
                    break
            if interval_end < interval_start:
                continue

            seconds = (interval_end - interval_start).total_seconds()
            for recess_start, recess_end in self._recesses:
                overlap_start = max(interval_start, recess_start)
                overlap_end = min(interval_end, recess_end)
                if overlap_end > overlap_start:
                    seconds -= (overlap_end - overlap_start).total_seconds()
            result[top_number] = max(0, int(seconds / 60))
        return result

    def _comparison_view(self) -> List[Dict[str, Any]]:
        actual_order = [item for item in self._order if item not in self._withdrawn]
        actual_positions = {
            item: position for position, item in enumerate(actual_order, start=1)
        }
        durations = self._actual_minutes()
        rows = []
        for planned_position, item in enumerate(self._top_level, start=1):
            withdrawn = item in self._withdrawn
            actual_position = None if withdrawn else actual_positions[item]
            if withdrawn:
                status = "withdrawn"
            elif actual_position == planned_position:
                status = "as_planned"
            else:
                status = "moved"
            rows.append(
                {
                    "item": item,
                    "planned_position": planned_position,
                    "actual_position": actual_position,
                    "expected_minutes": self._items[item].get("expected_minutes", 0),
                    "actual_minutes": durations[item],
                    "status": status,
                }
            )
        return rows

    def documents(self) -> Dict[str, Any]:
        return {
            "public_agenda": self._agenda_view(chair=False),
            "chair_run_sheet": self._agenda_view(chair=True),
            "minutes": self._minutes_view(),
            "actions": self._actions_view(),
            "comparison": self._comparison_view(),
            "errors": sorted(deepcopy(self._errors)),
        }
