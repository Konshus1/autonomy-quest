"""Packaging release approval assessment and deterministic dossier export."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from typing import Any


_REGION_FIELDS = ("text_claims", "colors", "images")
_REVIEW_FIELDS = {
    "id",
    "candidate",
    "department",
    "decision",
    "scope",
    "comment",
    "supersedes",
}
_DECISIONS = {"approve", "reject", "comment"}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _validate_candidate(candidate: dict[str, Any]) -> None:
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")

    for field in ("id", "based_on", "regions"):
        if field not in candidate:
            raise ValueError(f"candidate missing {field}")

    if not isinstance(candidate["id"], str) or not candidate["id"]:
        raise ValueError("candidate id must be a non-empty string")
    if candidate["based_on"] is not None and not isinstance(
        candidate["based_on"], str
    ):
        raise ValueError("candidate based_on must be a string or null")
    if not isinstance(candidate["regions"], dict):
        raise ValueError("candidate regions must be an object")

    required_fields = set(_REGION_FIELDS) | {"checksum"}
    for name, region in candidate["regions"].items():
        if not isinstance(name, str) or not isinstance(region, dict):
            raise ValueError(
                "region names must be strings and regions must be objects"
            )
        if set(region) != required_fields:
            raise ValueError(
                f"region {name} must contain exactly {sorted(required_fields)}"
            )

        supplied = region["checksum"]
        if not isinstance(supplied, str) or len(supplied) != 64:
            raise ValueError(f"region {name} has an invalid checksum")

        payload = {field: region[field] for field in _REGION_FIELDS}
        expected = hashlib.sha256(_json_bytes(payload)).hexdigest()
        if supplied != expected:
            raise ValueError(f"region {name} checksum mismatch")


def _candidate_index(
    candidate: dict[str, Any],
    prior_candidates: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(prior_candidates, dict):
        raise ValueError("prior_candidates must be an object")

    _validate_candidate(candidate)
    snapshots: dict[str, dict[str, Any]] = {candidate["id"]: candidate}

    for key, prior in prior_candidates.items():
        if not isinstance(key, str):
            raise ValueError("prior candidate IDs must be strings")
        _validate_candidate(prior)
        if prior["id"] != key:
            raise ValueError(
                f"prior candidate key {key} does not match its id"
            )
        if key == candidate["id"]:
            raise ValueError(f"duplicate candidate id {key}")
        snapshots[key] = prior

    return snapshots


def _lineage(
    candidate: dict[str, Any],
    snapshots: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    lineage = [candidate]
    seen = {candidate["id"]}
    parent_id = candidate["based_on"]

    while parent_id is not None:
        if parent_id in seen:
            raise ValueError("candidate lineage contains a cycle")
        parent = snapshots.get(parent_id)
        if parent is None:
            raise ValueError(f"missing prior candidate {parent_id}")
        lineage.append(parent)
        seen.add(parent_id)
        parent_id = parent["based_on"]

    lineage.reverse()
    return lineage


def _region_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return _json_bytes(a) == _json_bytes(b)


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, list[str]]:
    """Compare complete region snapshots from two candidates."""
    _validate_candidate(a)
    _validate_candidate(b)

    a_names = set(a["regions"])
    b_names = set(b["regions"])
    common = a_names & b_names

    return {
        "added": sorted(b_names - a_names),
        "removed": sorted(a_names - b_names),
        "changed": sorted(
            name
            for name in common
            if not _region_equal(
                a["regions"][name], b["regions"][name]
            )
        ),
        "unchanged": sorted(
            name
            for name in common
            if _region_equal(
                a["regions"][name], b["regions"][name]
            )
        ),
    }


def _validate_policy(
    policy: dict[str, Any],
) -> tuple[list[str], list[str]]:
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    if set(policy) != {"required_departments", "required_regions"}:
        raise ValueError(
            "policy must contain required_departments and required_regions"
        )

    departments = policy["required_departments"]
    regions = policy["required_regions"]
    if (
        not isinstance(departments, list)
        or not all(isinstance(item, str) for item in departments)
        or not isinstance(regions, list)
        or not all(isinstance(item, str) for item in regions)
    ):
        raise ValueError("policy requirements must be lists of strings")

    return sorted(set(departments)), sorted(set(regions))


def _validate_reviews(
    reviews: list[dict[str, Any]],
    candidate_ids: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(reviews, list):
        raise ValueError("reviews must be a list")

    checked: list[dict[str, Any]] = []
    ids: set[str] = set()

    for review in reviews:
        if not isinstance(review, dict) or set(review) != _REVIEW_FIELDS:
            raise ValueError(
                f"each review must contain exactly {sorted(_REVIEW_FIELDS)}"
            )

        for field in ("id", "candidate", "department", "comment"):
            if not isinstance(review[field], str):
                raise ValueError(f"review {field} must be a string")

        review_id = review["id"]
        if not review_id or review_id in ids:
            raise ValueError(f"duplicate or empty review id {review_id!r}")
        if review["candidate"] not in candidate_ids:
            raise ValueError(
                f"review {review_id} references unknown candidate"
            )
        if review["decision"] not in _DECISIONS:
            raise ValueError(
                f"invalid review decision {review['decision']!r}"
            )

        scope = review["scope"]
        if (
            not isinstance(scope, list)
            or not all(isinstance(name, str) for name in scope)
            or scope != sorted(scope)
            or len(scope) != len(set(scope))
        ):
            raise ValueError(
                "review scope must be a sorted list of unique region names"
            )

        supersedes = review["supersedes"]
        if supersedes is not None and not isinstance(supersedes, str):
            raise ValueError("review supersedes must be a string or null")

        ids.add(review_id)
        checked.append(review)

    return checked


def _active_reviews(
    reviews: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    active: dict[str, dict[str, Any]] = {}
    superseded: set[str] = set()

    for review in reviews:
        prior_id = review["supersedes"]
        if prior_id is not None:
            prior = by_id.get(prior_id)
            if prior is None:
                raise ValueError(
                    f"review {review['id']} supersedes an unknown or later review"
                )
            if prior["department"] != review["department"]:
                raise ValueError(
                    f"review {review['id']} supersedes a different department"
                )
            superseded.add(prior_id)

        by_id[review["id"]] = review
        active[review["department"]] = review

    return active, sorted(superseded)


def _usable_scope(
    review: dict[str, Any],
    lineage: list[dict[str, Any]],
) -> tuple[set[str], list[str]]:
    positions = {
        snapshot["id"]: index for index, snapshot in enumerate(lineage)
    }
    start = positions.get(review["candidate"])

    if start is None:
        return set(), [
            f"{review['id']} stale: {name}" for name in review["scope"]
        ]

    usable: set[str] = set()
    stale: list[str] = []

    for name in review["scope"]:
        previous = lineage[start]["regions"].get(name)
        valid = previous is not None

        for snapshot in lineage[start + 1 :]:
            current = snapshot["regions"].get(name)
            if (
                previous is None
                or current is None
                or not _region_equal(previous, current)
            ):
                valid = False
            previous = current

        if valid:
            usable.add(name)
        else:
            stale.append(f"{review['id']} stale: {name}")

    return usable, stale


def _ancestry(lineage: list[dict[str, Any]]) -> list[str]:
    current = lineage[-1]
    lines: list[str] = []

    for region_name in sorted(current["regions"]):
        events: list[tuple[str, str, str | None]] = []
        previous_region: dict[str, Any] | None = None
        previous_id: str | None = None

        for snapshot in lineage:
            region = snapshot["regions"].get(region_name)
            if region is None:
                previous_region = None
                previous_id = snapshot["id"]
                continue

            if previous_region is None:
                events.append(("introduced", snapshot["id"], None))
            elif not _region_equal(previous_region, region):
                events.append(("changed", snapshot["id"], previous_id))

            previous_region = region
            previous_id = snapshot["id"]

        kind, event_id, old_id = events[-1]
        if event_id == current["id"]:
            if kind == "changed":
                lines.append(
                    f"{region_name}: changed in {current['id']} "
                    f"(previous {old_id})"
                )
            else:
                lines.append(
                    f"{region_name}: introduced in {current['id']}"
                )
        else:
            lines.append(
                f"{region_name}: unchanged from {event_id} "
                f"through {current['id']}"
            )

    return sorted(lines)


def assess(
    candidate: dict[str, Any],
    reviews: list[dict[str, Any]],
    policy: dict[str, Any],
    prior_candidates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Assess release readiness without mutating supplied snapshots."""
    snapshots = _candidate_index(candidate, prior_candidates)
    lineage = _lineage(candidate, snapshots)
    departments, required_regions = _validate_policy(policy)

    for region_name in required_regions:
        if region_name not in candidate["regions"]:
            raise ValueError(
                f"required region {region_name} is absent from candidate"
            )

    checked_reviews = _validate_reviews(reviews, set(snapshots))
    active, superseded = _active_reviews(checked_reviews)

    blockers: list[str] = []
    stale: list[str] = []

    for department in departments:
        review = active.get(department)
        if review is None:
            blockers.append(f"{department} missing approval")
            continue

        usable, review_stale = _usable_scope(review, lineage)
        stale.extend(review_stale)
        required_usable = usable.intersection(required_regions)

        if review["decision"] == "reject" and required_usable:
            blockers.append(
                f"{department} rejected: {review['comment']}"
            )
        elif review["decision"] != "approve":
            blockers.append(f"{department} missing approval")
        else:
            for region_name in required_regions:
                if region_name not in usable:
                    blockers.append(
                        f"{department} approval lacks {region_name}"
                    )

    blockers.sort()
    stale.sort()
    return {
        "ready": not blockers,
        "blockers": blockers,
        "stale": stale,
        "superseded": superseded,
        "ancestry": _ancestry(lineage),
    }


def export_dossier(
    candidate: dict[str, Any],
    reviews: list[dict[str, Any]],
    policy: dict[str, Any],
    prior_candidates: dict[str, dict[str, Any]],
) -> bytes:
    """Return a deterministic, self-contained stored ZIP dossier."""
    candidate_snapshot = copy.deepcopy(candidate)
    reviews_snapshot = copy.deepcopy(reviews)
    readiness = assess(candidate, reviews, policy, prior_candidates)

    members = [
        ("candidate.json", _json_bytes(candidate_snapshot)),
        ("reviews.json", _json_bytes(reviews_snapshot)),
        ("readiness.json", _json_bytes(readiness)),
    ]
    checksums = {
        name: hashlib.sha256(payload).hexdigest()
        for name, payload in members
    }
    members.append(("checksums.json", _json_bytes(checksums)))

    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as dossier:
        for name, payload in members:
            info = zipfile.ZipInfo(
                name,
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 0
            info.external_attr = 0o600 << 16
            dossier.writestr(info, payload)

    return output.getvalue()
