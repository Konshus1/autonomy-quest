from datetime import date
from itertools import combinations


def _overlaps(start_a, end_a, start_b, end_b):
    return start_a <= end_b and start_b <= end_a


def _shift_years(value, years):
    target_year = value.year + years
    try:
        return value.replace(year=target_year)
    except ValueError:
        if value.month == 2 and value.day == 29:
            return date(target_year, 2, 28)
        raise


def plan_season(proposal, observations, next_season_start):
    beds = proposal["beds"]
    crops = proposal["crops"]
    assignments = proposal["assignments"]
    volunteers = proposal["volunteers"]
    previous_crops = proposal["previous_crops"]

    ordered_assignments = sorted(assignments, key=lambda item: item["id"])
    assignments_by_bed = {bed_id: [] for bed_id in sorted(beds)}
    for assignment in ordered_assignments:
        assignments_by_bed.setdefault(assignment["bed"], []).append(assignment)

    issue_keys = set()

    def add_issue(code, assignment_ids, bed_id):
        issue_keys.add((code, bed_id, tuple(sorted(assignment_ids))))

    for bed_id in sorted(assignments_by_bed):
        bed_assignments = assignments_by_bed[bed_id]
        for first, second in combinations(bed_assignments, 2):
            if _overlaps(
                first["plant"],
                first["harvest"],
                second["plant"],
                second["harvest"],
            ):
                add_issue(
                    "OCCUPANCY_OVERLAP",
                    [first["id"], second["id"]],
                    bed_id,
                )

    neighbor_pairs = set()
    for bed_id in sorted(beds):
        for neighbor_id in beds[bed_id]["neighbors"]:
            if neighbor_id in beds and neighbor_id != bed_id:
                neighbor_pairs.add(tuple(sorted((bed_id, neighbor_id))))

    for first_bed, second_bed in sorted(neighbor_pairs):
        for first in assignments_by_bed.get(first_bed, []):
            for second in assignments_by_bed.get(second_bed, []):
                if not _overlaps(
                    first["plant"],
                    first["harvest"],
                    second["plant"],
                    second["harvest"],
                ):
                    continue

                first_crop = crops[first["crop"]]
                second_crop = crops[second["crop"]]
                incompatible = (
                    second_crop["family"]
                    in first_crop["incompatible_families"]
                    or first_crop["family"]
                    in second_crop["incompatible_families"]
                )
                if incompatible:
                    add_issue(
                        "NEIGHBOR_CONFLICT",
                        [first["id"], second["id"]],
                        first_bed,
                    )

    unavailable_observations = sorted(
        (item for item in observations if item["kind"] == "bed_unavailable"),
        key=lambda item: (item["bed"], item["start"], item["end"]),
    )

    for assignment in ordered_assignments:
        bed_id = assignment["bed"]
        crop = crops[assignment["crop"]]

        plant_start, plant_end = crop["plant"]
        if not plant_start <= assignment["plant"] <= plant_end:
            add_issue("OUTSIDE_PLANTING_WINDOW", [assignment["id"]], bed_id)

        harvest_start, harvest_end = crop["harvest"]
        if not harvest_start <= assignment["harvest"] <= harvest_end:
            add_issue("OUTSIDE_HARVEST_WINDOW", [assignment["id"]], bed_id)

        required_predecessor = crop["required_predecessor"]
        if (
            required_predecessor is not None
            and required_predecessor not in previous_crops.get(bed_id, [])
        ):
            add_issue("PREDECESSOR_MISSING", [assignment["id"]], bed_id)

        volunteer = volunteers.get(assignment["volunteer"])
        if (
            volunteer is not None
            and volunteer["needs_accessible"]
            and not beds[bed_id]["accessible"]
        ):
            add_issue("INACCESSIBLE_ASSIGNMENT", [assignment["id"]], bed_id)

        for observation in unavailable_observations:
            if observation["bed"] != bed_id:
                continue
            if _overlaps(
                assignment["plant"],
                assignment["harvest"],
                observation["start"],
                observation["end"],
            ):
                add_issue("BED_UNAVAILABLE", [assignment["id"]], bed_id)

    issues = [
        {"code": code, "assignments": list(assignment_ids), "bed": bed_id}
        for code, bed_id, assignment_ids in sorted(
            issue_keys,
            key=lambda item: (item[0], item[1], item[2]),
        )
    ]

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
    work_cards.sort(key=lambda card: (card["date"], card["kind"], card["assignment"]))

    volunteer_views = {volunteer_id: [] for volunteer_id in sorted(volunteers)}
    for card in work_cards:
        if card["volunteer"] in volunteer_views:
            volunteer_views[card["volunteer"]].append(dict(card))

    bed_calendar = {bed_id: [] for bed_id in sorted(beds)}
    for assignment in ordered_assignments:
        bed_calendar[assignment["bed"]].append(
            {
                "assignment": assignment["id"],
                "crop": assignment["crop"],
                "start": assignment["plant"],
                "end": assignment["harvest"],
            }
        )
    for entries in bed_calendar.values():
        entries.sort(key=lambda entry: (entry["start"], entry["end"], entry["assignment"]))

    if ordered_assignments:
        earliest_planting_year = min(
            assignment["plant"].year for assignment in ordered_assignments
        )
        year_offset = next_season_start.year - earliest_planting_year
    else:
        year_offset = 0

    next_season = []
    for assignment in ordered_assignments:
        if assignment["locked"]:
            plant = assignment["plant"]
            harvest = assignment["harvest"]
            automatic_changes = []
        else:
            plant = _shift_years(assignment["plant"], year_offset)
            harvest = _shift_years(assignment["harvest"], year_offset)
            automatic_changes = (
                ["plant", "harvest"]
                if plant != assignment["plant"] or harvest != assignment["harvest"]
                else []
            )

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
