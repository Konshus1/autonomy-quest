"""Packaging release approval, comparison, and deterministic dossier export."""

import hashlib
import io
import json
import zipfile


_MISSING = object()
_DECISIONS = {"approve", "reject", "comment"}


def _json_bytes(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _region_checksum(region):
    payload = {
        "text_claims": region["text_claims"],
        "colors": region["colors"],
        "images": region["images"],
    }
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def _validate_candidate(candidate):
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")
    if set(candidate) != {"id", "based_on", "regions"}:
        raise ValueError("candidate must contain id, based_on, and regions")
    if not isinstance(candidate["id"], str) or not candidate["id"]:
        raise ValueError("candidate id must be a non-empty string")
    if candidate["based_on"] is not None and not isinstance(candidate["based_on"], str):
        raise ValueError("candidate based_on must be a string or null")
    if not isinstance(candidate["regions"], dict):
        raise ValueError("candidate regions must be an object")

    for name, region in candidate["regions"].items():
        if not isinstance(name, str) or not name:
            raise ValueError("region names must be non-empty strings")
        if not isinstance(region, dict):
            raise ValueError("region must be an object")
        if set(region) != {"text_claims", "colors", "images", "checksum"}:
            raise ValueError("region has invalid fields")
        checksum = region["checksum"]
        if not isinstance(checksum, str):
            raise ValueError("region checksum must be a string")
        if checksum != checksum.lower() or checksum != _region_checksum(region):
            raise ValueError("invalid checksum for region %s" % name)


def _lineage(candidate, prior_candidates):
    if not isinstance(prior_candidates, dict):
        raise ValueError("prior_candidates must be an object")

    _validate_candidate(candidate)
    for candidate_id, snapshot in prior_candidates.items():
        if not isinstance(candidate_id, str):
            raise ValueError("prior candidate IDs must be strings")
        _validate_candidate(snapshot)
        if snapshot["id"] != candidate_id:
            raise ValueError("prior candidate key does not match its ID")

    result = []
    seen = set()
    current = candidate
    while True:
        candidate_id = current["id"]
        if candidate_id in seen:
            raise ValueError("candidate lineage contains a cycle")
        seen.add(candidate_id)
        result.append(current)

        parent_id = current["based_on"]
        if parent_id is None:
            break
        if parent_id not in prior_candidates:
            raise ValueError("missing prior candidate %s" % parent_id)
        current = prior_candidates[parent_id]

    result.reverse()
    return result


def _validate_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    if set(policy) != {"required_departments", "required_regions"}:
        raise ValueError("policy has invalid fields")

    departments = policy["required_departments"]
    regions = policy["required_regions"]
    if not isinstance(departments, list) or not all(isinstance(x, str) for x in departments):
        raise ValueError("required_departments must be a list of strings")
    if not isinstance(regions, list) or not all(isinstance(x, str) for x in regions):
        raise ValueError("required_regions must be a list of strings")
    if departments != sorted(set(departments)):
        raise ValueError("required_departments must be sorted and unique")
    if regions != sorted(set(regions)):
        raise ValueError("required_regions must be sorted and unique")


def _validate_reviews(reviews, known_candidates):
    if not isinstance(reviews, list):
        raise ValueError("reviews must be a list")

    by_id = {}
    positions = {}
    required_fields = {
        "id",
        "candidate",
        "department",
        "decision",
        "scope",
        "comment",
        "supersedes",
    }

    for position, review in enumerate(reviews):
        if not isinstance(review, dict) or set(review) != required_fields:
            raise ValueError("review has invalid fields")
        review_id = review["id"]
        if not isinstance(review_id, str) or not review_id or review_id in by_id:
            raise ValueError("review IDs must be unique non-empty strings")
        if review["candidate"] not in known_candidates:
            raise ValueError("review references an unknown candidate")
        if not isinstance(review["department"], str) or not review["department"]:
            raise ValueError("review department must be a non-empty string")
        if review["decision"] not in _DECISIONS:
            raise ValueError("invalid review decision")
        if not isinstance(review["comment"], str):
            raise ValueError("review comment must be a string")

        scope = review["scope"]
        if not isinstance(scope, list) or not all(isinstance(x, str) for x in scope):
            raise ValueError("review scope must be a list of strings")
        if scope != sorted(set(scope)):
            raise ValueError("review scope must be sorted and unique")
        snapshot_regions = known_candidates[review["candidate"]]["regions"]
        if any(region not in snapshot_regions for region in scope):
            raise ValueError("review scope references an absent region")

        supersedes = review["supersedes"]
        if supersedes is not None:
            if supersedes not in by_id:
                raise ValueError("a review may supersede only a prior review")
            if by_id[supersedes]["department"] != review["department"]:
                raise ValueError("a review may supersede only its own department")

        by_id[review_id] = review
        positions[review_id] = position

    return by_id, positions


def _region_changed_after(lineage, start_index, region_name):
    previous = lineage[start_index]["regions"].get(region_name, _MISSING)
    for snapshot in lineage[start_index + 1:]:
        current = snapshot["regions"].get(region_name, _MISSING)
        if current != previous:
            return True
        previous = current
    return False


def _ancestry(lineage):
    current = lineage[-1]
    lines = []

    for region_name in sorted(current["regions"]):
        current_region = current["regions"][region_name]
        if len(lineage) == 1:
            lines.append("%s: introduced in %s" % (region_name, current["id"]))
            continue

        previous = lineage[-2]
        previous_region = previous["regions"].get(region_name, _MISSING)
        if previous_region is _MISSING:
            lines.append("%s: introduced in %s" % (region_name, current["id"]))
        elif previous_region != current_region:
            lines.append(
                "%s: changed in %s (previous %s)"
                % (region_name, current["id"], previous["id"])
            )
        else:
            start = len(lineage) - 2
            while start > 0:
                earlier = lineage[start - 1]["regions"].get(region_name, _MISSING)
                if earlier != current_region:
                    break
                start -= 1
            lines.append(
                "%s: unchanged from %s through %s"
                % (region_name, lineage[start]["id"], current["id"])
            )

    return lines


def assess(candidate, reviews, policy, prior_candidates):
    """Assess a packaging candidate under the supplied review policy."""
    lineage = _lineage(candidate, prior_candidates)
    _validate_policy(policy)

    known_candidates = dict(prior_candidates)
    known_candidates[candidate["id"]] = candidate
    by_id, positions = _validate_reviews(reviews, known_candidates)

    lineage_indexes = {snapshot["id"]: index for index, snapshot in enumerate(lineage)}
    superseded = {
        review["supersedes"]
        for review in reviews
        if review["supersedes"] is not None
    }

    applicable = []
    stale_by_review = {}
    stale_lines = []
    for review in reviews:
        review_id = review["id"]
        if review_id in superseded or review["candidate"] not in lineage_indexes:
            continue

        start_index = lineage_indexes[review["candidate"]]
        stale_regions = {
            region
            for region in review["scope"]
            if _region_changed_after(lineage, start_index, region)
        }
        stale_by_review[review_id] = stale_regions
        stale_lines.extend(
            "%s stale: %s" % (review_id, region)
            for region in sorted(stale_regions)
        )
        applicable.append(review)

    controlling = {}
    for review in applicable:
        department = review["department"]
        prior = controlling.get(department)
        if prior is None or positions[review["id"]] > positions[prior["id"]]:
            controlling[department] = review

    blockers = []
    required_regions = policy["required_regions"]
    for department in policy["required_departments"]:
        review = controlling.get(department)
        if review is None:
            blockers.append("%s missing approval" % department)
            continue

        usable_scope = set(review["scope"]) - stale_by_review[review["id"]]
        if review["decision"] == "reject" and (
            not required_regions or usable_scope.intersection(required_regions)
        ):
            blockers.append("%s rejected: %s" % (department, review["comment"]))
        elif review["decision"] != "approve":
            blockers.append("%s missing approval" % department)
        else:
            for region in required_regions:
                if region not in usable_scope:
                    blockers.append("%s approval lacks %s" % (department, region))

    blockers.sort()
    stale_lines.sort()
    superseded_ids = sorted(superseded)
    ancestry = _ancestry(lineage)

    return {
        "ready": not blockers,
        "blockers": blockers,
        "stale": stale_lines,
        "superseded": superseded_ids,
        "ancestry": ancestry,
    }


def compare(a, b):
    """Compare complete region snapshots from two candidates."""
    _validate_candidate(a)
    _validate_candidate(b)

    a_names = set(a["regions"])
    b_names = set(b["regions"])
    common = a_names & b_names
    return {
        "added": sorted(b_names - a_names),
        "removed": sorted(a_names - b_names),
        "changed": sorted(name for name in common if a["regions"][name] != b["regions"][name]),
        "unchanged": sorted(name for name in common if a["regions"][name] == b["regions"][name]),
    }


def export_dossier(candidate, reviews, policy, prior_candidates):
    """Return a byte-for-byte deterministic, self-contained ZIP dossier."""
    readiness = assess(candidate, reviews, policy, prior_candidates)
    payloads = {
        "candidate.json": _json_bytes(candidate),
        "reviews.json": _json_bytes(reviews),
        "readiness.json": _json_bytes(readiness),
    }
    checksums = {
        name: hashlib.sha256(payloads[name]).hexdigest()
        for name in ("candidate.json", "reviews.json", "readiness.json")
    }
    payloads["checksums.json"] = _json_bytes(checksums)

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in (
            "candidate.json",
            "reviews.json",
            "readiness.json",
            "checksums.json",
        ):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 0
            info.external_attr = 0o600 << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, payloads[name])
    return output.getvalue()
