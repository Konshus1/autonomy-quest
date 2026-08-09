import csv
import hashlib
import io
import json
import math
from datetime import datetime, timezone


_FIELDS = (
    "id",
    "observer",
    "local_time",
    "latitude",
    "longitude",
    "species",
    "count",
    "photo_hash",
    "notes",
)


class _UnionFind:
    def __init__(self, members):
        self.parent = {member: member for member in members}

    def find(self, member):
        parent = self.parent[member]
        if parent != member:
            self.parent[member] = self.find(parent)
        return self.parent[member]

    def union(self, left, right):
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return left_root
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        return left_root


class Survey:
    def __init__(self, time_tolerance_seconds, coordinate_tolerance):
        self.time_tolerance_seconds = self._nonnegative_number(
            time_tolerance_seconds, "time_tolerance_seconds"
        )
        self.coordinate_tolerance = self._nonnegative_number(
            coordinate_tolerance, "coordinate_tolerance"
        )
        self._records = {}
        self._decisions = {}
        self._alias_parent = {}

    @staticmethod
    def _nonnegative_number(value, name):
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a nonnegative number") from exc
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{name} must be a nonnegative number")
        return number

    @staticmethod
    def _field_string(value):
        if value is None:
            return ""
        return value if isinstance(value, str) else str(value)

    @staticmethod
    def _parse_time(value):
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid local_time: {value!r}") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("local_time must include an explicit UTC offset")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _parse_float(value, field):
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {field}: {value!r}") from exc
        if not math.isfinite(parsed):
            raise ValueError(f"invalid {field}: {value!r}")
        return parsed

    @staticmethod
    def _parse_count(value):
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid count: {value!r}") from exc

    def _prepare_record(self, row, source):
        if not hasattr(row, "get"):
            raise ValueError("each observation must be a mapping")

        original = {field: self._field_string(row.get(field, "")) for field in _FIELDS}
        observation_id = original["id"]
        if not observation_id:
            raise ValueError("observation IDs must be nonempty")

        return {
            "original": original,
            "source": source,
            "utc_time": self._parse_time(original["local_time"]),
            "latitude": self._parse_float(original["latitude"], "latitude"),
            "longitude": self._parse_float(original["longitude"], "longitude"),
            "count": self._parse_count(original["count"]),
            "species_key": original["species"].casefold(),
        }

    def _import_rows(self, rows, source):
        prepared = []
        batch_ids = set()
        for row in rows:
            record = self._prepare_record(row, source)
            observation_id = record["original"]["id"]
            if observation_id in self._records or observation_id in batch_ids:
                raise ValueError(f"duplicate observation ID: {observation_id}")
            batch_ids.add(observation_id)
            prepared.append((observation_id, record))

        for observation_id, record in prepared:
            self._records[observation_id] = record

    def import_csv(self, text):
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames is None or any(field not in reader.fieldnames for field in _FIELDS):
            raise ValueError("CSV must contain all required columns")
        self._import_rows(reader, "csv")

    def import_json(self, rows):
        if isinstance(rows, (str, bytes, bytearray)):
            raise ValueError("import_json expects an iterable of dictionaries")
        self._import_rows(rows, "json")

    def set_aliases(self, mapping):
        if not hasattr(mapping, "items"):
            raise ValueError("aliases must be supplied as a mapping")

        parent = {}

        def find(label):
            parent.setdefault(label, label)
            if parent[label] != label:
                parent[label] = find(parent[label])
            return parent[label]

        def union(left, right):
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                return
            if right_root < left_root:
                left_root, right_root = right_root, left_root
            parent[right_root] = left_root

        for key, values in mapping.items():
            if isinstance(values, (str, bytes)):
                raise ValueError("each alias mapping value must be a list of spellings")
            key_label = self._field_string(key).casefold()
            find(key_label)
            try:
                aliases = list(values)
            except TypeError as exc:
                raise ValueError("each alias mapping value must be iterable") from exc
            for value in aliases:
                alias_label = self._field_string(value).casefold()
                union(key_label, alias_label)

        self._alias_parent = {label: find(label) for label in parent}

    def _alias_root(self, label):
        return self._alias_parent.get(label, label)

    def _species_compatible(self, left, right):
        return self._alias_root(left["species_key"]) == self._alias_root(
            right["species_key"]
        )

    def _match_reason(self, left, right):
        left_hash = left["original"]["photo_hash"]
        right_hash = right["original"]["photo_hash"]
        if left_hash and left_hash == right_hash:
            return "photo"

        elapsed = abs((left["utc_time"] - right["utc_time"]).total_seconds())
        if elapsed > self.time_tolerance_seconds:
            return None

        latitude_delta = left["latitude"] - right["latitude"]
        longitude_delta = left["longitude"] - right["longitude"]
        distance = math.hypot(latitude_delta, longitude_delta)
        if distance > self.coordinate_tolerance:
            return None

        if not self._species_compatible(left, right):
            return None
        return "possible"

    @staticmethod
    def _group_id(members):
        joined = ",".join(members).encode("utf-8")
        return hashlib.sha256(joined).hexdigest()[:16]

    def _candidate_groups(self):
        observation_ids = sorted(self._records)
        union_find = _UnionFind(observation_ids)

        for index, left_id in enumerate(observation_ids):
            for right_id in observation_ids[index + 1 :]:
                if self._match_reason(self._records[left_id], self._records[right_id]):
                    union_find.union(left_id, right_id)

        components = {}
        for observation_id in observation_ids:
            root = union_find.find(observation_id)
            components.setdefault(root, []).append(observation_id)

        groups = []
        for members in components.values():
            if len(members) < 2:
                continue
            members = sorted(members)
            member_key = tuple(members)
            if member_key in self._decisions:
                continue

            hashes = {
                self._records[member]["original"]["photo_hash"] for member in members
            }
            reason = "photo" if len(hashes) == 1 and "" not in hashes else "possible"
            groups.append(
                {
                    "group_id": self._group_id(members),
                    "members": members,
                    "reason": reason,
                }
            )

        groups.sort(key=lambda group: (group["members"], group["group_id"]))
        return groups

    def possible_duplicates(self):
        return [
            {
                "group_id": group["group_id"],
                "members": list(group["members"]),
                "reason": group["reason"],
            }
            for group in self._candidate_groups()
        ]

    def decide(self, member_ids, decision):
        if decision not in ("same", "distinct"):
            raise ValueError("decision must be 'same' or 'distinct'")
        try:
            supplied = tuple(sorted(member_ids))
        except TypeError as exc:
            raise ValueError("member_ids must be iterable") from exc
        if not supplied or len(supplied) != len(set(supplied)):
            raise ValueError("member_ids must identify one current duplicate group")

        current = {tuple(group["members"]) for group in self._candidate_groups()}
        if supplied not in current:
            raise ValueError("member_ids must exactly match a currently reported group")

        if decision == "same":
            supplied_set = set(supplied)
            for members, prior_decision in self._decisions.items():
                if prior_decision == "distinct" and set(members).issubset(supplied_set):
                    raise ValueError("same decision conflicts with a prior distinct decision")

        self._decisions[supplied] = decision

    def _would_collapse_distinct_group(self, union_find, left_root, right_root):
        for members, decision in self._decisions.items():
            if decision != "distinct":
                continue
            roots = {union_find.find(member) for member in members}
            roots.discard(left_root)
            roots.discard(right_root)
            if not roots:
                return True
        return False

    def _resolved_components(self):
        observation_ids = sorted(self._records)
        union_find = _UnionFind(observation_ids)

        for members, decision in sorted(self._decisions.items()):
            if decision == "same":
                representative = members[0]
                for member in members[1:]:
                    union_find.union(representative, member)

        photo_groups = {}
        for observation_id in observation_ids:
            photo_hash = self._records[observation_id]["original"]["photo_hash"]
            if photo_hash:
                photo_groups.setdefault(photo_hash, []).append(observation_id)

        photo_edges = []
        for members in photo_groups.values():
            members.sort()
            for index, left in enumerate(members):
                for right in members[index + 1 :]:
                    photo_edges.append((left, right))

        for left, right in sorted(photo_edges):
            left_root = union_find.find(left)
            right_root = union_find.find(right)
            if left_root == right_root:
                continue
            if self._would_collapse_distinct_group(union_find, left_root, right_root):
                continue
            union_find.union(left_root, right_root)

        components = {}
        for observation_id in observation_ids:
            root = union_find.find(observation_id)
            components.setdefault(root, []).append(observation_id)
        return [sorted(members) for members in components.values()]

    def summaries(self):
        daily = {}
        site = {}
        species = {}

        components = sorted(self._resolved_components(), key=lambda members: members[0])
        for members in components:
            representative_id = members[0]
            representative = self._records[representative_id]
            contribution = max(self._records[member]["count"] for member in members)

            daily_key = representative["utc_time"].date().isoformat()
            latitude = representative["original"]["latitude"]
            longitude = representative["original"]["longitude"]
            site_key = f"{latitude},{longitude}"
            species_key = representative["original"]["species"]

            daily[daily_key] = daily.get(daily_key, 0) + contribution
            site[site_key] = site.get(site_key, 0) + contribution
            species[species_key] = species.get(species_key, 0) + contribution

        return {
            "daily": dict(sorted(daily.items())),
            "site": dict(sorted(site.items())),
            "species": dict(sorted(species.items())),
        }

    def _decision_rows(self):
        return [
            {"members": list(members), "decision": decision}
            for members, decision in sorted(self._decisions.items())
        ]

    def canonical_export(self):
        observations = []
        for observation_id in sorted(self._records):
            original = self._records[observation_id]["original"]
            observations.append({field: original[field] for field in _FIELDS})
        payload = {
            "observations": observations,
            "decisions": self._decision_rows(),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def reconciliation_report(self):
        return {
            "groups": self.possible_duplicates(),
            "decisions": self._decision_rows(),
        }

    def original_row(self, observation_id):
        if observation_id not in self._records:
            raise KeyError(observation_id)
        record = self._records[observation_id]
        result = dict(record["original"])
        result["source"] = record["source"]
        return result
