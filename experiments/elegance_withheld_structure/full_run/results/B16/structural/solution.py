"""Deterministic garden-season planning."""

from datetime import date


def _overlaps(start_a, end_a, start_b, end_b):
    return start_a <= end_b and start_b <= end_a


def _issue(code, assignment_ids, bed):
    return {
        "code": code,
        "assignments": sorted(assignment_ids),
        "bed": bed,
    }


def _shift_year(value, years):
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def plan_season(proposal, observations, next_season_start):
    beds = proposal["beds"]
    crops = proposal["crops"]
    assignments = proposal["assignments"]
    volunteers = proposal["volunteers"]
    previous_crops = proposal["previous_crops"]

    ordered_assignments = sorted(assignments, key=lambda item: item["id"])
    issues = []

    for assignment in ordered_assignments:
        assignment_id = assignment["id"]
        bed_id = assignment["bed"]
        crop = crops[assignment["crop"]]

        if not crop["plant"][0] <= assignment["plant"] <= crop["plant"][1]:
            issues.append(
                _issue("OUTSIDE_PLANTING_WINDOW", [assignment_id], bed_id)
            )

        if not crop["harvest"][0] <= assignment["harvest"] <= crop["harvest"][1]:
            issues.append(
                _issue("OUTSIDE_HARVEST_WINDOW", [assignment_id], bed_id)
            )

        required = crop["required_predecessor"]
        if required is not None and required not in previous_crops.get(bed_id, []):
            issues.append(_issue("PREDECESSOR_MISSING", [assignment_id], bed_id))

        volunteer = volunteers[assignment["volunteer"]]
        if volunteer["needs_accessible"] and not beds[bed_id]["accessible"]:
            issues.append(
                _issue("INACCESSIBLE_ASSIGNMENT", [assignment_id], bed_id)
            )

        for observation in observations:
            if (
                observation["kind"] == "bed_unavailable"
                and observation["bed"] == bed_id
                and _overlaps(
                    assignment["plant"],
                    assignment["harvest"],
                    observation["start"],
                    observation["end"],
                )
            ):
                issues.append(_issue("BED_UNAVAILABLE", [assignment_id], bed_id))

    for index, first in enumerate(ordered_assignments):
        for second in ordered_assignments[index + 1:]:
            if not _overlaps(
                first["plant"],
                first["harvest"],
                second["plant"],
                second["harvest"],
            ):
                continue

            if first["bed"] == second["bed"]:
                issues.append(
                    _issue(
                        "OCCUPANCY_OVERLAP",
                        [first["id"], second["id"]],
                        first["bed"],
                    )
                )
                continue

            first_bed = first["bed"]
            second_bed = second["bed"]
            are_neighbors = (
                second_bed in beds[first_bed]["neighbors"]
                or first_bed in beds[second_bed]["neighbors"]
            )
            if not are_neighbors:
                continue

            first_crop = crops[first["crop"]]
            second_crop = crops[second["crop"]]
            incompatible = (
                second_crop["family"] in first_crop["incompatible_families"]
                or first_crop["family"] in second_crop["incompatible_families"]
            )
            if incompatible:
                issues.append(
                    _issue(
                        "NEIGHBOR_CONFLICT",
                        [first["id"], second["id"]],
                        min(first_bed, second_bed),
                    )
                )

    issues.sort(
        key=lambda item: (item["code"], item["bed"], item["assignments"])
    )

    work_cards = []
    for assignment in ordered_assignments:
        work_cards.append(
            {
                "date": assignment["plant"],
                "kind": "plant",
                "assignment": assignment["id"],
                "bed": assignment["bed"],
                "volunteer": assignment["volunteer"],
            }
        )
        work_cards.append(
            {
                "date": assignment["harvest"],
                "kind": "harvest",
                "assignment": assignment["id"],
                "bed": assignment["bed"],
                "volunteer": assignment["volunteer"],
            }
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

    bed_calendar = {}
    for bed_id in sorted(beds):
        entries = [
            {
                "assignment": assignment["id"],
                "crop": assignment["crop"],
                "start": assignment["plant"],
                "end": assignment["harvest"],
            }
            for assignment in ordered_assignments
            if assignment["bed"] == bed_id
        ]
        entries.sort(
            key=lambda entry: (
                entry["start"],
                entry["end"],
                entry["assignment"],
            )
        )
        bed_calendar[bed_id] = entries

    next_season = []
    if ordered_assignments:
        earliest_year = min(
            assignment["plant"].year for assignment in ordered_assignments
        )
        year_offset = next_season_start.year - earliest_year

        for assignment in ordered_assignments:
            if assignment["locked"]:
                plant = assignment["plant"]
                harvest = assignment["harvest"]
                automatic_changes = []
            else:
                plant = _shift_year(assignment["plant"], year_offset)
                harvest = _shift_year(assignment["harvest"], year_offset)
                if (
                    plant != assignment["plant"]
                    or harvest != assignment["harvest"]
                ):
                    automatic_changes = ["plant", "harvest"]
                else:
                    automatic_changes = []

            next_season.append(
                {
                    "assignment": assignment["id"],
                    "bed": assignment["bed"],
                    "crop": assignment["crop"],
                    "plant": plant,
                    "harvest": harvest,
                    "volunteer": assignment["volunteer"],
                    "locked": assignment["locked"],
                    "automatic_changes": automatic_changes,
                }
            )

    return {
        "issues": issues,
        "work_cards": work_cards,
        "volunteer_views": volunteer_views,
        "bed_calendar": bed_calendar,
        "next_season": next_season,
    }
