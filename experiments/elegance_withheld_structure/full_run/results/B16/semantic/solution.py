"""Deterministic seasonal garden planning using only the standard library."""

from __future__ import annotations

from datetime import date
from itertools import combinations
from typing import Any


def _overlaps(start_a: date, end_a: date, start_b: date, end_b: date) -> bool:
    """Return whether two inclusive date intervals overlap."""
    return start_a <= end_b and start_b <= end_a


def _shift_year(value: date, years: int) -> date:
    """Shift a date by whole calendar years, clamping February 29 to February 28."""
    target_year = value.year + years
    try:
        return value.replace(year=target_year)
    except ValueError:
        return value.replace(year=target_year, day=28)


def plan_season(
    proposal: dict[str, Any],
    observations: list[dict[str, Any]],
    next_season_start: date,
) -> dict[str, Any]:
    """Validate a proposal and produce deterministic current- and next-season views."""
    beds = proposal["beds"]
    crops = proposal["crops"]
    volunteers = proposal["volunteers"]
    previous_crops = proposal["previous_crops"]
    assignments = sorted(proposal["assignments"], key=lambda item: item["id"])

    issues: set[tuple[str, str, tuple[str, ...]]] = set()

    def add_issue(code: str, bed: str, assignment_ids: list[str]) -> None:
        issues.add((code, bed, tuple(sorted(assignment_ids))))

    assignments_by_bed: dict[str, list[dict[str, Any]]] = {
        bed_id: [] for bed_id in sorted(beds)
    }
    for assignment in assignments:
        assignments_by_bed.setdefault(assignment["bed"], []).append(assignment)

    for bed_id in sorted(assignments_by_bed):
        bed_assignments = assignments_by_bed[bed_id]
        for left, right in combinations(bed_assignments, 2):
            if _overlaps(
                left["plant"], left["harvest"],
                right["plant"], right["harvest"],
            ):
                add_issue(
                    "OCCUPANCY_OVERLAP",
                    bed_id,
                    [left["id"], right["id"]],
                )

    neighbor_edges: set[tuple[str, str]] = set()
    for bed_id in sorted(beds):
        for neighbor_id in beds[bed_id]["neighbors"]:
            if neighbor_id != bed_id:
                neighbor_edges.add(tuple(sorted((bed_id, neighbor_id))))

    for left_bed, right_bed in sorted(neighbor_edges):
        for left in assignments_by_bed.get(left_bed, []):
            for right in assignments_by_bed.get(right_bed, []):
                if not _overlaps(
                    left["plant"], left["harvest"],
                    right["plant"], right["harvest"],
                ):
                    continue
                left_crop = crops[left["crop"]]
                right_crop = crops[right["crop"]]
                incompatible = (
                    right_crop["family"] in left_crop["incompatible_families"]
                    or left_crop["family"] in right_crop["incompatible_families"]
                )
                if incompatible:
                    add_issue(
                        "NEIGHBOR_CONFLICT",
                        left_bed,
                        [left["id"], right["id"]],
                    )

    unavailable_observations = sorted(
        (item for item in observations if item["kind"] == "bed_unavailable"),
        key=lambda item: (item["bed"], item["start"], item["end"]),
    )

    for assignment in assignments:
        assignment_id = assignment["id"]
        bed_id = assignment["bed"]
        crop = crops[assignment["crop"]]

        plant_start, plant_end = crop["plant"]
        if not plant_start <= assignment["plant"] <= plant_end:
            add_issue("OUTSIDE_PLANTING_WINDOW", bed_id, [assignment_id])

        harvest_start, harvest_end = crop["harvest"]
        if not harvest_start <= assignment["harvest"] <= harvest_end:
            add_issue("OUTSIDE_HARVEST_WINDOW", bed_id, [assignment_id])

        required_predecessor = crop["required_predecessor"]
        if (
            required_predecessor is not None
            and required_predecessor not in previous_crops.get(bed_id, [])
        ):
            add_issue("PREDECESSOR_MISSING", bed_id, [assignment_id])

        volunteer = volunteers[assignment["volunteer"]]
        if volunteer["needs_accessible"] and not beds[bed_id]["accessible"]:
            add_issue("INACCESSIBLE_ASSIGNMENT", bed_id, [assignment_id])

        for observation in unavailable_observations:
            if observation["bed"] != bed_id:
                continue
            if _overlaps(
                assignment["plant"], assignment["harvest"],
                observation["start"], observation["end"],
            ):
                add_issue("BED_UNAVAILABLE", bed_id, [assignment_id])

    issue_list = [
        {"code": code, "assignments": list(ids), "bed": bed_id}
        for code, bed_id, ids in sorted(
            issues,
            key=lambda item: (item[0], item[1], item[2]),
        )
    ]

    work_cards: list[dict[str, Any]] = []
    for assignment in assignments:
        common = {
            "assignment": assignment["id"],
            "bed": assignment["bed"],
            "volunteer": assignment["volunteer"],
        }
        work_cards.append(
            {"date": assignment["plant"], "kind": "plant", **common}
        )
        work_cards.append(
            {"date": assignment["harvest"], "kind": "harvest", **common}
        )
    work_cards.sort(
        key=lambda card: (card["date"], card["kind"], card["assignment"])
    )

    volunteer_views = {
        volunteer_id: [
            dict(card)
            for card in work_cards
            if card["volunteer"] == volunteer_id
        ]
        for volunteer_id in sorted(volunteers)
    }

    bed_calendar: dict[str, list[dict[str, Any]]] = {}
    for bed_id in sorted(beds):
        entries = [
            {
                "assignment": assignment["id"],
                "crop": assignment["crop"],
                "start": assignment["plant"],
                "end": assignment["harvest"],
            }
            for assignment in assignments_by_bed.get(bed_id, [])
        ]
        entries.sort(
            key=lambda entry: (
                entry["start"], entry["end"], entry["assignment"]
            )
        )
        bed_calendar[bed_id] = entries

    earliest_planting_year = min(
        assignment["plant"].year for assignment in assignments
    ) if assignments else next_season_start.year
    year_offset = next_season_start.year - earliest_planting_year

    next_season: list[dict[str, Any]] = []
    for assignment in assignments:
        plant = assignment["plant"]
        harvest = assignment["harvest"]
        automatic_changes: list[str] = []

        if not assignment["locked"]:
            shifted_plant = _shift_year(plant, year_offset)
            shifted_harvest = _shift_year(harvest, year_offset)
            if shifted_plant != plant or shifted_harvest != harvest:
                automatic_changes = ["plant", "harvest"]
            plant = shifted_plant
            harvest = shifted_harvest

        next_season.append({
            "assignment": assignment["id"],
            "bed": assignment["bed"],
            "crop": assignment["crop"],
            "plant": plant,
            "harvest": harvest,
            "volunteer": assignment["volunteer"],
            "locked": assignment["locked"],
            "automatic_changes": automatic_changes,
        })

    return {
        "issues": issue_list,
        "work_cards": work_cards,
        "volunteer_views": volunteer_views,
        "bed_calendar": bed_calendar,
        "next_season": next_season,
    }
