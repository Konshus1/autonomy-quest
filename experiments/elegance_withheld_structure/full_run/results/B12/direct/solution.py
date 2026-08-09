import csv
import hashlib
import io
import json
import math
from copy import deepcopy
from datetime import datetime, timezone


class Survey:
    FIELDS = (
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

    def __init__(self, time_tolerance_seconds, coordinate_tolerance):
        self.time_tolerance_seconds = float(time_tolerance_seconds)
        self.coordinate_tolerance = float(coordinate_tolerance)
        if self.time_tolerance_seconds < 0 or self.coordinate_tolerance < 0:
            raise ValueError("tolerances must be nonnegative")

        self._observations = {}
        self._original_rows = {}
        self._decisions = {}
        self._alias_parent = {}

    def import_csv(self, text):
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or tuple(reader.fieldnames) != self.FIELDS:
            raise ValueError("CSV columns do not match the required columns")

        prepared = []
        for row in reader:
            if None in row:
                raise ValueError("CSV row has extra columns")
            prepared.append(self._prepare_row(row, "csv"))
        self._commit(prepared)

    def import_json(self, rows):
        if isinstance(rows, (str, bytes)) or not hasattr(rows, "__iter__"):
            raise TypeError("rows must be an iterable of dictionaries")

        prepared = []
        required = set(self.FIELDS) - {"photo_hash", "notes"}
        allowed = set(self.FIELDS)

        for row in rows:
            if not isinstance(row, dict):
                raise TypeError("each JSON row must be a dictionary")
            if set(row) - allowed or required - set(row):
                raise ValueError("JSON row fields do not match the required fields")

            complete = {field: row.get(field, "") for field in self.FIELDS}
            prepared.append(self._prepare_row(complete, "json"))

        self._commit(prepared)

    def set_aliases(self, mapping):
        if not isinstance(mapping, dict):
            raise TypeError("mapping must be a dictionary")

        parent = {}

        def find(value):
            parent.setdefault(value, value)
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(left, right):
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                return
            if left_root > right_root:
                left_root, right_root = right_root, left_root
            parent[right_root] = left_root

        for key, values in mapping.items():
            if isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
                raise TypeError("each alias value must be a list of spellings")

            spellings = [str(key).casefold()]
            spellings.extend(str(value).casefold() for value in values)
            for spelling in spellings:
                find(spelling)
            for spelling in spellings[1:]:
                union(spellings[0], spelling)

        self._alias_parent = {value: find(value) for value in parent}

    def possible_duplicates(self):
        groups = []
        for members, reason in self._candidate_groups():
            if members not in self._decisions:
                groups.append(self._group_dict(members, reason))
        return groups

    def decide(self, member_ids, decision):
        if decision not in ("same", "distinct"):
            raise ValueError("decision must be 'same' or 'distinct'")
        if isinstance(member_ids, (str, bytes)):
            raise ValueError("member_ids must exactly match a reported group")

        members = tuple(sorted(member_ids))
        if len(members) < 2 or len(set(members)) != len(members):
            raise ValueError("member_ids must exactly match a reported group")

        reported = {
            tuple(group["members"])
            for group in self.possible_duplicates()
        }
        if members not in reported:
            raise ValueError("member_ids must exactly match a currently reported group")

        self._decisions[members] = decision

    def summaries(self):
        parent = {
            observation_id: observation_id
            for observation_id in self._observations
        }

        def find(value):
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(members):
            roots = [find(member) for member in members if member in parent]
            if not roots:
                return
            representative = min(roots)
            for root in roots:
                parent[root] = representative

        distinct_sets = [
            set(members)
            for members, decision in self._decisions.items()
            if decision == "distinct"
        ]

        for members, reason in self._candidate_groups():
            if reason != "photo":
                continue
            member_set = set(members)
            if any(decided <= member_set for decided in distinct_sets):
                continue
            union(members)

        for members, decision in sorted(self._decisions.items()):
            if decision == "same":
                union(members)

        components = {}
        for observation_id in sorted(parent):
            components.setdefault(find(observation_id), []).append(observation_id)

        daily = {}
        site = {}
        species = {}

        for members in sorted(components.values(), key=lambda value: value[0]):
            representative_id = min(members)
            representative = self._observations[representative_id]
            count = max(
                self._observations[member]["count"]
                for member in members
            )

            daily_key = representative["utc_time"].date().isoformat()
            site_key = (
                representative["latitude_text"]
                + ","
                + representative["longitude_text"]
            )
            species_key = representative["species"]

            daily[daily_key] = daily.get(daily_key, 0) + count
            site[site_key] = site.get(site_key, 0) + count
            species[species_key] = species.get(species_key, 0) + count

        return {
            "daily": dict(sorted(daily.items())),
            "site": dict(sorted(site.items())),
            "species": dict(sorted(species.items())),
        }

    def canonical_export(self):
        observations = [
            deepcopy(self._original_rows[observation_id])
            for observation_id in sorted(self._original_rows)
        ]
        decisions = [
            {"members": list(members), "decision": decision}
            for members, decision in sorted(self._decisions.items())
        ]
        return json.dumps(
            {"observations": observations, "decisions": decisions},
            sort_keys=True,
            separators=(",", ":"),
        )

    def reconciliation_report(self):
        return {
            "groups": self.possible_duplicates(),
            "decisions": [
                {"members": list(members), "decision": decision}
                for members, decision in sorted(self._decisions.items())
            ],
        }

    def original_row(self, observation_id):
        if observation_id not in self._original_rows:
            raise KeyError(observation_id)
        return deepcopy(self._original_rows[observation_id])

    def _prepare_row(self, row, source):
        original = {}
        for field in self.FIELDS:
            value = row.get(field, "")
            if value is None and field in ("photo_hash", "notes"):
                value = ""
            original[field] = str(value)

        observation_id = original["id"]
        if not observation_id:
            raise ValueError("id must be nonempty")

        try:
            local_time = datetime.fromisoformat(original["local_time"])
        except ValueError as error:
            raise ValueError("local_time must be ISO-8601") from error

        if local_time.tzinfo is None or local_time.utcoffset() is None:
            raise ValueError("local_time must include an explicit UTC offset")

        try:
            latitude = float(original["latitude"])
            longitude = float(original["longitude"])
            count = int(original["count"])
        except ValueError as error:
            raise ValueError("latitude, longitude, and count must be numeric") from error

        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise ValueError("coordinates must be finite")

        original["source"] = source
        parsed = {
            "id": observation_id,
            "utc_time": local_time.astimezone(timezone.utc),
            "latitude": latitude,
            "longitude": longitude,
            "latitude_text": original["latitude"],
            "longitude_text": original["longitude"],
            "species": original["species"],
            "count": count,
            "photo_hash": original["photo_hash"],
        }
        return observation_id, parsed, original

    def _commit(self, prepared):
        ids = [item[0] for item in prepared]
        if len(ids) != len(set(ids)):
            raise ValueError("ids must be unique")
        if any(observation_id in self._observations for observation_id in ids):
            raise ValueError("ids must be unique")

        for observation_id, parsed, original in prepared:
            self._observations[observation_id] = parsed
            self._original_rows[observation_id] = original

    def _species_compatible(self, left, right):
        left_key = left.casefold()
        right_key = right.casefold()
        if left_key == right_key:
            return True
        return (
            left_key in self._alias_parent
            and right_key in self._alias_parent
            and self._alias_parent[left_key] == self._alias_parent[right_key]
        )

    def _candidate_groups(self):
        observations = self._observations
        ids = sorted(observations)
        groups = []

        by_hash = {}
        for observation_id in ids:
            photo_hash = observations[observation_id]["photo_hash"]
            if photo_hash:
                by_hash.setdefault(photo_hash, []).append(observation_id)

        for members in by_hash.values():
            if len(members) > 1:
                groups.append((tuple(sorted(members)), "photo"))

        parent = {observation_id: observation_id for observation_id in ids}
        touched = set()

        def find(value):
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(left, right):
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                return
            if left_root > right_root:
                left_root, right_root = right_root, left_root
            parent[right_root] = left_root

        for index, left_id in enumerate(ids):
            left = observations[left_id]
            for right_id in ids[index + 1:]:
                right = observations[right_id]

                if (
                    left["photo_hash"]
                    and left["photo_hash"] == right["photo_hash"]
                ):
                    continue

                time_difference = abs(
                    (left["utc_time"] - right["utc_time"]).total_seconds()
                )
                if time_difference > self.time_tolerance_seconds:
                    continue

                coordinate_distance = math.hypot(
                    left["latitude"] - right["latitude"],
                    left["longitude"] - right["longitude"],
                )
                if coordinate_distance > self.coordinate_tolerance:
                    continue

                if not self._species_compatible(
                    left["species"], right["species"]
                ):
                    continue

                union(left_id, right_id)
                touched.update((left_id, right_id))

        components = {}
        for observation_id in sorted(touched):
            components.setdefault(find(observation_id), []).append(observation_id)

        for members in components.values():
            if len(members) > 1:
                groups.append((tuple(sorted(members)), "possible"))

        deduplicated = {}
        for members, reason in groups:
            if members not in deduplicated or reason == "photo":
                deduplicated[members] = reason

        return sorted(deduplicated.items(), key=lambda item: item[0])

    @staticmethod
    def _group_dict(members, reason):
        group_id = hashlib.sha256(
            ",".join(members).encode("utf-8")
        ).hexdigest()[:16]
        return {
            "group_id": group_id,
            "members": list(members),
            "reason": reason,
        }
