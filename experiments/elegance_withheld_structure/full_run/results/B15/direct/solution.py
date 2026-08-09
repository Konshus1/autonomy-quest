import hashlib
import io
import json
import zipfile


def _json_bytes(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _validate_region(region, where):
    expected_keys = {"text_claims", "colors", "images", "checksum"}
    if not isinstance(region, dict) or set(region) != expected_keys:
        raise ValueError(f"invalid region at {where}")

    checksum = region["checksum"]
    if not isinstance(checksum, str) or len(checksum) != 64 or checksum != checksum.lower():
        raise ValueError(f"invalid checksum at {where}")
    try:
        int(checksum, 16)
    except ValueError as exc:
        raise ValueError(f"invalid checksum at {where}") from exc

    material = {
        "text_claims": region["text_claims"],
        "colors": region["colors"],
        "images": region["images"],
    }
    expected = hashlib.sha256(_json_bytes(material)).hexdigest()
    if checksum != expected:
        raise ValueError(f"checksum mismatch at {where}")


def _validate_candidate(candidate, where="candidate"):
    expected_keys = {"id", "based_on", "regions"}
    if not isinstance(candidate, dict) or set(candidate) != expected_keys:
        raise ValueError(f"invalid {where}")

    candidate_id = candidate["id"]
    based_on = candidate["based_on"]
    regions = candidate["regions"]

    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError(f"invalid {where} id")
    if based_on is not None and (not isinstance(based_on, str) or not based_on):
        raise ValueError(f"invalid {where} based_on")
    if not isinstance(regions, dict):
        raise ValueError(f"invalid {where} regions")
    if any(not isinstance(name, str) or not name for name in regions):
        raise ValueError(f"invalid region name in {where}")

    for name, region in regions.items():
        _validate_region(region, f"{candidate_id}.{name}")


def _validated_candidates(candidate, prior_candidates):
    if not isinstance(prior_candidates, dict):
        raise ValueError("prior_candidates must be a mapping")

    _validate_candidate(candidate)
    all_candidates = {}

    for candidate_id, snapshot in prior_candidates.items():
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("invalid prior candidate key")
        _validate_candidate(snapshot, f"prior candidate {candidate_id}")
        if snapshot["id"] != candidate_id:
            raise ValueError(f"prior candidate key/id mismatch for {candidate_id}")
        all_candidates[candidate_id] = snapshot

    if candidate["id"] in all_candidates:
        raise ValueError("current candidate is also present in prior_candidates")
    all_candidates[candidate["id"]] = candidate
    return all_candidates


def _candidate_chain(candidate, all_candidates):
    reverse_chain = [candidate]
    seen = {candidate["id"]}
    parent_id = candidate["based_on"]

    while parent_id is not None:
        if parent_id in seen:
            raise ValueError("candidate lineage contains a cycle")
        parent = all_candidates.get(parent_id)
        if parent is None:
            raise ValueError(f"missing prior candidate {parent_id}")
        reverse_chain.append(parent)
        seen.add(parent_id)
        parent_id = parent["based_on"]

    reverse_chain.reverse()
    return reverse_chain


def _validate_policy(policy):
    expected_keys = {"required_departments", "required_regions"}
    if not isinstance(policy, dict) or set(policy) != expected_keys:
        raise ValueError("invalid policy")

    departments = policy["required_departments"]
    regions = policy["required_regions"]
    for values, label in (
        (departments, "required_departments"),
        (regions, "required_regions"),
    ):
        if not isinstance(values, list):
            raise ValueError(f"invalid policy {label}")
        if any(not isinstance(item, str) or not item for item in values):
            raise ValueError(f"invalid policy {label}")
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate policy {label}")

    return sorted(departments), sorted(regions)


def _review_state(reviews, known_candidate_ids):
    if not isinstance(reviews, list):
        raise ValueError("reviews must be a list")

    expected_keys = {
        "id",
        "candidate",
        "department",
        "decision",
        "scope",
        "comment",
        "supersedes",
    }
    by_id = {}
    submissions = []
    superseded = set()

    for index, review in enumerate(reviews):
        if not isinstance(review, dict) or set(review) != expected_keys:
            raise ValueError(f"invalid review at index {index}")

        review_id = review["id"]
        candidate_id = review["candidate"]
        department = review["department"]
        decision = review["decision"]
        scope = review["scope"]
        comment = review["comment"]
        target_id = review["supersedes"]

        if not isinstance(review_id, str) or not review_id or review_id in by_id:
            raise ValueError(f"invalid or duplicate review id at index {index}")
        if candidate_id not in known_candidate_ids:
            raise ValueError(f"review {review_id} names an unknown candidate")
        if not isinstance(department, str) or not department:
            raise ValueError(f"invalid department in review {review_id}")
        if decision not in {"approve", "reject", "comment"}:
            raise ValueError(f"invalid decision in review {review_id}")
        if not isinstance(scope, list):
            raise ValueError(f"invalid scope in review {review_id}")
        if any(not isinstance(item, str) or not item for item in scope):
            raise ValueError(f"invalid scope in review {review_id}")
        if scope != sorted(scope) or len(scope) != len(set(scope)):
            raise ValueError(f"scope is not a sorted unique list in review {review_id}")
        if not isinstance(comment, str):
            raise ValueError(f"invalid comment in review {review_id}")

        if target_id is not None:
            if not isinstance(target_id, str) or target_id not in by_id:
                raise ValueError(f"review {review_id} does not supersede a prior review")
            target = by_id[target_id]
            if target["department"] != department:
                raise ValueError(f"review {review_id} supersedes another department")
            superseded.add(target_id)

        by_id[review_id] = review
        submissions.append((index, review))

    active = [
        (index, review)
        for index, review in submissions
        if review["id"] not in superseded
    ]
    return active, sorted(superseded)


def _region_unchanged(chain, start_index, region_name):
    if region_name not in chain[start_index]["regions"]:
        return False

    for index in range(start_index, len(chain) - 1):
        before = chain[index]["regions"]
        after = chain[index + 1]["regions"]
        if region_name not in before or region_name not in after:
            return False
        if before[region_name] != after[region_name]:
            return False

    return True


def _ancestry(chain):
    current = chain[-1]
    lines = []

    for region_name in sorted(current["regions"]):
        if len(chain) == 1 or region_name not in chain[-2]["regions"]:
            lines.append(f"{region_name}: introduced in {current['id']}")
            continue

        if current["regions"][region_name] != chain[-2]["regions"][region_name]:
            lines.append(
                f"{region_name}: changed in {current['id']} "
                f"(previous {chain[-2]['id']})"
            )
            continue

        start = len(chain) - 2
        while start > 0:
            older = chain[start - 1]["regions"]
            newer = chain[start]["regions"]
            if region_name not in older or older[region_name] != newer[region_name]:
                break
            start -= 1

        lines.append(
            f"{region_name}: unchanged from {chain[start]['id']} "
            f"through {current['id']}"
        )

    return sorted(lines)


def assess(candidate, reviews, policy, prior_candidates):
    all_candidates = _validated_candidates(candidate, prior_candidates)
    chain = _candidate_chain(candidate, all_candidates)
    required_departments, required_regions = _validate_policy(policy)
    active, superseded = _review_state(reviews, set(all_candidates))

    chain_index = {
        snapshot["id"]: index
        for index, snapshot in enumerate(chain)
    }

    controlling = {}
    for index, review in active:
        if review["candidate"] in chain_index:
            controlling[review["department"]] = (index, review)

    usable_by_department = {}
    stale = []

    for department in sorted(controlling):
        review = controlling[department][1]
        start_index = chain_index[review["candidate"]]
        usable = set()

        for region_name in review["scope"]:
            if _region_unchanged(chain, start_index, region_name):
                usable.add(region_name)
            else:
                stale.append(f"{review['id']} stale: {region_name}")

        usable_by_department[department] = usable

    blockers = []
    required_region_set = set(required_regions)

    for department in required_departments:
        selected = controlling.get(department)
        if selected is None:
            blockers.append(f"{department} missing approval")
            continue

        review = selected[1]
        usable = usable_by_department[department]

        if review["decision"] == "reject" and usable & required_region_set:
            blockers.append(f"{department} rejected: {review['comment']}")
        elif review["decision"] != "approve":
            blockers.append(f"{department} missing approval")
        else:
            for region_name in required_regions:
                if region_name not in usable:
                    blockers.append(f"{department} approval lacks {region_name}")

    blockers.sort()
    stale.sort()

    return {
        "ready": not blockers,
        "blockers": blockers,
        "stale": stale,
        "superseded": superseded,
        "ancestry": _ancestry(chain),
    }


def compare(a, b):
    _validate_candidate(a, "candidate a")
    _validate_candidate(b, "candidate b")

    a_names = set(a["regions"])
    b_names = set(b["regions"])
    common = a_names & b_names

    return {
        "added": sorted(b_names - a_names),
        "removed": sorted(a_names - b_names),
        "changed": sorted(
            name
            for name in common
            if a["regions"][name] != b["regions"][name]
        ),
        "unchanged": sorted(
            name
            for name in common
            if a["regions"][name] == b["regions"][name]
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
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 0
            info.external_attr = 0o600 << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, data)

    return output.getvalue()
