import hashlib
import io
import json
import zipfile


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _region_equal(left, right):
    return _json_bytes(left) == _json_bytes(right)


def _validate_candidate(candidate):
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")
    if not isinstance(candidate.get("id"), str):
        raise ValueError("candidate id must be a string")
    if candidate.get("based_on") is not None and not isinstance(candidate.get("based_on"), str):
        raise ValueError("candidate based_on must be a string or null")

    regions = candidate.get("regions")
    if not isinstance(regions, dict):
        raise ValueError("candidate regions must be an object")

    for name, region in regions.items():
        if not isinstance(name, str) or not isinstance(region, dict):
            raise ValueError("region names and snapshots must be JSON objects")
        required = {"text_claims", "colors", "images", "checksum"}
        if set(region) != required:
            raise ValueError("region %s has invalid fields" % name)
        checksum = region["checksum"]
        if not isinstance(checksum, str):
            raise ValueError("region %s checksum must be a string" % name)
        payload = {
            "text_claims": region["text_claims"],
            "colors": region["colors"],
            "images": region["images"],
        }
        expected = hashlib.sha256(_json_bytes(payload)).hexdigest()
        if checksum != expected:
            raise ValueError("region %s checksum mismatch" % name)


def _candidate_context(candidate, prior_candidates):
    _validate_candidate(candidate)
    if not isinstance(prior_candidates, dict):
        raise ValueError("prior_candidates must be an object")

    candidates = {}
    for key, snapshot in prior_candidates.items():
        if not isinstance(key, str):
            raise ValueError("prior candidate IDs must be strings")
        _validate_candidate(snapshot)
        if snapshot["id"] != key:
            raise ValueError("prior candidate key does not match candidate id")
        candidates[key] = snapshot

    current_id = candidate["id"]
    if current_id in candidates:
        raise ValueError("current candidate also appears in prior_candidates")
    candidates[current_id] = candidate

    reverse_lineage = []
    seen = set()
    cursor = candidate
    while cursor is not None:
        cursor_id = cursor["id"]
        if cursor_id in seen:
            raise ValueError("candidate lineage contains a cycle")
        seen.add(cursor_id)
        reverse_lineage.append(cursor)
        parent_id = cursor["based_on"]
        if parent_id is None:
            cursor = None
        else:
            if parent_id not in candidates:
                raise ValueError("missing prior candidate %s" % parent_id)
            cursor = candidates[parent_id]

    lineage = list(reversed(reverse_lineage))
    positions = {snapshot["id"]: index for index, snapshot in enumerate(lineage)}
    return candidates, lineage, positions


def _validate_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    departments = policy.get("required_departments")
    regions = policy.get("required_regions")
    if not isinstance(departments, list) or not all(isinstance(item, str) for item in departments):
        raise ValueError("required_departments must be a list of strings")
    if not isinstance(regions, list) or not all(isinstance(item, str) for item in regions):
        raise ValueError("required_regions must be a list of strings")
    return sorted(set(departments)), sorted(set(regions))


def _review_state(reviews, candidates, lineage_positions):
    if not isinstance(reviews, list):
        raise ValueError("reviews must be a list")

    by_id = {}
    ordered = []
    superseded = set()

    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            raise ValueError("review must be an object")
        required = {
            "id",
            "candidate",
            "department",
            "decision",
            "scope",
            "comment",
            "supersedes",
        }
        if set(review) != required:
            raise ValueError("review has invalid fields")

        review_id = review["id"]
        if not isinstance(review_id, str) or review_id in by_id:
            raise ValueError("review IDs must be unique strings")
        if review["candidate"] not in candidates:
            raise ValueError("review %s references an unknown candidate" % review_id)
        if not isinstance(review["department"], str):
            raise ValueError("review department must be a string")
        if review["decision"] not in {"approve", "reject", "comment"}:
            raise ValueError("invalid review decision")
        if not isinstance(review["comment"], str):
            raise ValueError("review comment must be a string")

        scope = review["scope"]
        if (
            not isinstance(scope, list)
            or not all(isinstance(region, str) for region in scope)
            or scope != sorted(scope)
            or len(scope) != len(set(scope))
        ):
            raise ValueError("review scope must be a sorted list of unique region names")
        snapshot_regions = candidates[review["candidate"]]["regions"]
        if any(region not in snapshot_regions for region in scope):
            raise ValueError("review scope contains a region absent from its candidate")

        prior_id = review["supersedes"]
        if prior_id is not None:
            if not isinstance(prior_id, str) or prior_id not in by_id:
                raise ValueError("supersedes must identify a prior review")
            prior = by_id[prior_id][1]
            if prior["department"] != review["department"]:
                raise ValueError("a review cannot supersede another department")
            superseded.add(prior_id)

        by_id[review_id] = (index, review)
        ordered.append((index, review))

    controlling = {}
    for index, review in ordered:
        if review["id"] in superseded:
            continue
        if review["candidate"] not in lineage_positions:
            continue
        department = review["department"]
        previous = controlling.get(department)
        if previous is None or index > previous[0]:
            controlling[department] = (index, review)

    return {department: item[1] for department, item in controlling.items()}, sorted(superseded)


def _usable_scope(review, lineage, positions):
    start = positions[review["candidate"]]
    usable = set()
    stale = set()

    for region_name in review["scope"]:
        unchanged = True
        for index in range(start, len(lineage) - 1):
            before = lineage[index]["regions"]
            after = lineage[index + 1]["regions"]
            if (
                region_name not in before
                or region_name not in after
                or not _region_equal(before[region_name], after[region_name])
            ):
                unchanged = False
                break
        if unchanged:
            usable.add(region_name)
        else:
            stale.add(region_name)

    return usable, stale


def _ancestry(lineage):
    current = lineage[-1]
    current_id = current["id"]
    lines = []

    for region_name in sorted(current["regions"]):
        if len(lineage) == 1 or region_name not in lineage[-2]["regions"]:
            lines.append("%s: introduced in %s" % (region_name, current_id))
            continue

        previous = lineage[-2]
        if not _region_equal(previous["regions"][region_name], current["regions"][region_name]):
            lines.append(
                "%s: changed in %s (previous %s)"
                % (region_name, current_id, previous["id"])
            )
            continue

        first = len(lineage) - 2
        while first > 0:
            older = lineage[first - 1]["regions"]
            newer = lineage[first]["regions"]
            if region_name not in older or not _region_equal(older[region_name], newer[region_name]):
                break
            first -= 1
        lines.append(
            "%s: unchanged from %s through %s"
            % (region_name, lineage[first]["id"], current_id)
        )

    return sorted(lines)


def assess(candidate, reviews, policy, prior_candidates):
    candidates, lineage, positions = _candidate_context(candidate, prior_candidates)
    required_departments, required_regions = _validate_policy(policy)
    controlling, superseded = _review_state(reviews, candidates, positions)

    blockers = []
    stale_entries = []

    for department in required_departments:
        review = controlling.get(department)
        if review is None:
            blockers.append("%s missing approval" % department)
            continue

        usable, stale_regions = _usable_scope(review, lineage, positions)

        if review["decision"] == "reject":
            if usable:
                blockers.append("%s rejected: %s" % (department, review["comment"]))
            else:
                blockers.append("%s missing approval" % department)
            continue

        if review["decision"] != "approve":
            blockers.append("%s missing approval" % department)
            continue

        for region_name in stale_regions:
            stale_entries.append("%s stale: %s" % (review["id"], region_name))
        for region_name in required_regions:
            if region_name not in usable:
                blockers.append("%s approval lacks %s" % (department, region_name))

    blockers.sort()
    stale_entries.sort()
    return {
        "ready": not blockers,
        "blockers": blockers,
        "stale": stale_entries,
        "superseded": superseded,
        "ancestry": _ancestry(lineage),
    }


def compare(a, b):
    _validate_candidate(a)
    _validate_candidate(b)
    a_regions = a["regions"]
    b_regions = b["regions"]
    a_names = set(a_regions)
    b_names = set(b_regions)
    shared = a_names & b_names

    return {
        "added": sorted(b_names - a_names),
        "removed": sorted(a_names - b_names),
        "changed": sorted(
            name for name in shared if not _region_equal(a_regions[name], b_regions[name])
        ),
        "unchanged": sorted(
            name for name in shared if _region_equal(a_regions[name], b_regions[name])
        ),
    }


def export_dossier(candidate, reviews, policy, prior_candidates):
    readiness = assess(candidate, reviews, policy, prior_candidates)
    members = [
        ("candidate.json", _json_bytes(candidate)),
        ("reviews.json", _json_bytes(reviews)),
        ("readiness.json", _json_bytes(readiness)),
    ]
    checksums = {
        name: hashlib.sha256(data).hexdigest()
        for name, data in members
    }
    members.append(("checksums.json", _json_bytes(checksums)))

    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 0
            info.external_attr = 0o600 << 16
            archive.writestr(info, data)
    return output.getvalue()
