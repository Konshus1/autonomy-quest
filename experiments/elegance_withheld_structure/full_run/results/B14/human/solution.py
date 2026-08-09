from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple


class Meeting:
    def __init__(self, agenda: Dict[str, Any]):
        self._agenda = deepcopy(agenda)
        self._items: Dict[str, Dict[str, Any]] = {}
        self._top_for: Dict[str, str] = {}
        self._descendants: Dict[str, Set[str]] = {}
        self._top_numbers: List[str] = []
        self._index_items(self._agenda.get("items", []))

        self._order = list(self._top_numbers)
        self._withdrawn: Set[str] = set()
        self._events: List[Dict[str, Any]] = []
        self._recesses: List[Tuple[datetime, datetime]] = []
        self._motions: Dict[str, str] = {}
        self._motion_counter = 0
        self._errors: List[str] = []
        self._latest_time: Optional[datetime] = None
        self._consent_membership = self._build_consent_membership(
            self._agenda.get("consent_groups", {})
        )

    def _index_items(self, items: List[Dict[str, Any]], top: Optional[str] = None) -> Set[str]:
        collected: Set[str] = set()
        for item in items:
            number = item["number"]
            item_top = number if top is None else top
            if top is None:
                self._top_numbers.append(number)
            self._items[number] = item
            self._top_for[number] = item_top
            children = self._index_items(item.get("subitems", []), item_top)
            descendants = {number} | children
            self._descendants[number] = descendants
            collected.update(descendants)
        return collected

    @staticmethod
    def _build_consent_membership(groups: Any) -> Dict[str, str]:
        membership: Dict[str, str] = {}
        if isinstance(groups, dict):
            iterable = groups.items()
        elif isinstance(groups, list):
            iterable = []
            for group in groups:
                if isinstance(group, dict) and "name" in group:
                    iterable.append((group["name"], group.get("items", [])))
        else:
            iterable = []

        for name, members in iterable:
            if isinstance(members, dict):
                members = members.get("items", [])
            if not isinstance(members, (list, tuple, set)):
                continue
            for member in members:
                if isinstance(member, dict):
                    member = member.get("number")
                if member is not None:
                    membership[str(member)] = str(name)
        return membership

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

    def _timestamp_is_invalid(self, at: Any) -> bool:
        if not isinstance(at, datetime):
            return True
        if self._latest_time is None:
            return False
        try:
            return at < self._latest_time
        except TypeError:
            return True

    def _add_common_errors(self, item: Any, at: Any) -> bool:
        invalid = False
        if item not in self._items:
            self._errors.append(f"unknown item {item}")
            invalid = True
        if self._timestamp_is_invalid(at):
            self._errors.append("timestamps out of order")
            invalid = True
        return invalid

    def _accept_time(self, at: datetime) -> None:
        self._latest_time = at

    def reorder(self, order: List[str], at: datetime) -> None:
        proposed = list(order) if isinstance(order, (list, tuple)) else []
        invalid = False

        for number in proposed:
            if number not in self._items:
                self._errors.append(f"unknown item {number}")
                invalid = True

        active = [number for number in self._top_numbers if number not in self._withdrawn]
        valid_members = (
            len(proposed) == len(set(proposed))
            and (
                set(proposed) == set(self._top_numbers)
                or set(proposed) == set(active)
            )
            and all(number in self._top_numbers for number in proposed)
        )
        if not valid_members:
            self._errors.append("invalid reorder")
            invalid = True
        if self._timestamp_is_invalid(at):
            self._errors.append("timestamps out of order")
            invalid = True
        if invalid:
            return

        self._order = proposed
        self._events.append({"type": "reorder", "order": proposed, "at": at})
        self._accept_time(at)

    def withdraw(self, item: str, at: datetime) -> None:
        if self._add_common_errors(item, at):
            return

        self._withdrawn.update(self._descendants[item])
        self._events.append({"type": "withdraw", "item": item, "at": at})
        self._accept_time(at)

    def add_motion(self, item: str, text: str, at: datetime) -> Optional[str]:
        if self._add_common_errors(item, at):
            return None

        self._motion_counter += 1
        ref = f"motion-{self._motion_counter}"
        self._motions[ref] = item
        self._events.append(
            {"type": "motion", "item": item, "text": text, "ref": ref, "at": at}
        )
        self._accept_time(at)
        return ref

    def record_vote(
        self, item: str, motion_ref: str, votes: Dict[str, str], at: datetime
    ) -> None:
        invalid = self._add_common_errors(item, at)

        if motion_ref not in self._motions or self._motions.get(motion_ref) != item:
            self._errors.append(f"unknown motion {motion_ref}")
            invalid = True

        if not isinstance(votes, dict) or any(
            vote not in {"yes", "no", "abstain"} for vote in votes.values()
        ):
            self._errors.append("invalid vote")
            invalid = True

        if invalid:
            return

        yes = sum(vote == "yes" for vote in votes.values())
        no = sum(vote == "no" for vote in votes.values())
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
                "votes": deepcopy(votes),
                "result": result,
                "at": at,
            }
        )
        self._accept_time(at)

    def add_recess(self, start: datetime, end: datetime) -> None:
        invalid = False
        try:
            ends_early = end < start
        except (TypeError, AttributeError):
            ends_early = True

        if ends_early:
            self._errors.append("recess ends before it starts")
            invalid = True
        if self._timestamp_is_invalid(start):
            self._errors.append("timestamps out of order")
            invalid = True
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            invalid = True
        if invalid:
            return

        minutes = int((end - start).total_seconds() // 60)
        self._events.append({"type": "recess", "minutes": minutes, "at": start})
        self._recesses.append((start, end))
        self._latest_time = end

    def _agenda_view(self, chair: bool) -> Dict[str, Any]:
        def convert_item(item: Dict[str, Any]) -> Dict[str, Any]:
            converted: Dict[str, Any] = {}
            for key, value in item.items():
                if key == "action":
                    continue
                if key == "private_notes" and not chair:
                    continue
                if key == "subitems":
                    converted[key] = [convert_item(child) for child in value]
                else:
                    converted[key] = self._iso(value)
            if chair:
                converted.setdefault("private_notes", None)
                converted["consent_group"] = self._consent_membership.get(item["number"])
            return converted

        view: Dict[str, Any] = {}
        for key, value in self._agenda.items():
            if key == "items":
                view[key] = [convert_item(item) for item in value]
            else:
                view[key] = self._iso(value)
        return view

    def _minutes_view(self) -> List[Dict[str, Any]]:
        return [self._iso(event) for event in self._events]

    def _actions_view(self) -> List[Dict[str, Any]]:
        actions = []
        for number, item in self._items.items():
            action = item.get("action")
            if action is not None and number not in self._withdrawn:
                actions.append({"item": number, "action": deepcopy(action)})
        actions.sort(key=lambda entry: entry["item"])
        return actions

    def _recess_overlap_minutes(self, start: datetime, end: datetime) -> int:
        seconds = 0.0
        for recess_start, recess_end in self._recesses:
            overlap_start = max(start, recess_start)
            overlap_end = min(end, recess_end)
            if overlap_end > overlap_start:
                seconds += (overlap_end - overlap_start).total_seconds()
        return int(seconds // 60)

    def _actual_minutes(self) -> Dict[str, int]:
        proceedings = [
            event for event in self._events if event["type"] in {"motion", "vote"}
        ]
        result = {number: 0 for number in self._top_numbers}
        if not proceedings:
            return result

        for top_number in self._top_numbers:
            first_index = next(
                (
                    index
                    for index, event in enumerate(proceedings)
                    if self._top_for[event["item"]] == top_number
                ),
                None,
            )
            if first_index is None:
                continue

            start = proceedings[first_index]["at"]
            end = proceedings[-1]["at"]
            for event in proceedings[first_index + 1 :]:
                if self._top_for[event["item"]] != top_number:
                    end = event["at"]
                    break

            gross = int((end - start).total_seconds() // 60)
            result[top_number] = max(
                0, gross - self._recess_overlap_minutes(start, end)
            )
        return result

    def _comparison_view(self) -> List[Dict[str, Any]]:
        actual_order = [
            number for number in self._order if number not in self._withdrawn
        ]
        actual_positions = {
            number: index + 1 for index, number in enumerate(actual_order)
        }
        durations = self._actual_minutes()
        comparison = []

        for planned_index, number in enumerate(self._top_numbers, start=1):
            withdrawn = number in self._withdrawn
            actual_position = None if withdrawn else actual_positions.get(number)
            if withdrawn:
                status = "withdrawn"
            elif actual_position == planned_index:
                status = "as_planned"
            else:
                status = "moved"

            comparison.append(
                {
                    "item": number,
                    "planned_position": planned_index,
                    "actual_position": actual_position,
                    "expected_minutes": self._items[number].get("expected_minutes"),
                    "actual_minutes": durations[number],
                    "status": status,
                }
            )
        return comparison

    def documents(self) -> Dict[str, Any]:
        result = {
            "public_agenda": self._agenda_view(chair=False),
            "chair_run_sheet": self._agenda_view(chair=True),
            "minutes": self._minutes_view(),
            "actions": self._actions_view(),
            "comparison": self._comparison_view(),
            "errors": sorted(self._errors),
        }
        return deepcopy(result)
