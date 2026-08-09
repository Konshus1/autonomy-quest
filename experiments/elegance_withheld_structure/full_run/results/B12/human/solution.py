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


class Survey:
    def __init__(self, time_tolerance_seconds, coordinate_tolerance):
        self.time_tolerance_seconds = float(time_tolerance_seconds)
        self.coordinate_tolerance = float(coordinate_tolerance)
        if self.time_tolerance_seconds < 0 or self.coordinate_tolerance < 0:
            raise ValueError("tolerances must be nonnegative")
        self._observations = {}
        self._decisions = {}
        self._alias_parent = {}

    def import_csv(self, text):
        if not isinstance(text, str):
            raise TypeError("CSV input must be text")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise ValueError("CSV input must contain a header")
        if len(reader.fieldnames) != len(_FIELDS) or set(reader.fieldnames) != set(_FIELDS):
            raise ValueError("CSV columns do not match the required schema")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("malformed CSV row")
            rows.append(self._prepare_row(row, "csv"))
        self._commit(rows)

    def import_json(self, rows):
        if isinstance(rows, (str, bytes, dict)):
            raise TypeError("JSON rows must be an iterable of dictionaries")
        prepared = []
        for row in rows:
            if not isinstance(row, dict):
                raise TypeError("each JSON row must be a dictionary")
            if len(row) != len(_FIELDS) or set(row) != set(_FIELDS):
                raise ValueError("JSON row fields do not match the required schema")
            converted = {
                field: "" if row[field] is None else str(row[field])
                for field in _FIELDS
            }
            prepared.append(self._prepare_row(converted, "json"))
        self._commit(prepared)

    def set_aliases(self, mapping):
        if not isinstance(mapping, dict):
            raise TypeError("aliases must be supplied as a mapping")
        parent = {}

        def add(value):
            key = value.casefold()
            parent.setdefault(key, key)
            return key

        def find(value):
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(left, right):
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                if left_root < right_root:
                    parent[right_root] = left_root
                else:
                    parent[left_root] = right_root

        for label, aliases in mapping.items():
            if not isinstance(label, str) or not isinstance(aliases, (list, tuple)):
                raise TypeError("alias keys must be strings and values must be lists")
            base = add(label)
            for alias in aliases:
                if not isinstance(alias, str):
                    raise TypeError("species aliases must be strings")
                union(base, add(alias))
        for key in list(parent):
            parent[key] = find(key)
        self._alias_parent = parent

    def possible_duplicates(self):
        groups = []
        for members, reason in self._candidate_groups():
            if members in self._decisions:
                continue
            groups.append(self._group_dict(members, reason))
        return groups

    def decide(self, member_ids, decision):
        if decision not in ("same", "distinct"):
            raise ValueError("decision must be 'same' or 'distinct'")
        if isinstance(member_ids, (str, bytes)):
            raise ValueError("member_ids must be a collection of IDs")
        members = tuple(sorted(member_ids))
        if len(members) < 2 or len(set(members)) != len(members):
            raise ValueError("member_ids must contain distinct observation IDs")
        current = {tuple(group["members"]) for group in self.possible_duplicates()}
        if members not in current:
            raise ValueError("member_ids must exactly match a currently reported group")
        self._decisions[members] = decision

    def summaries(self):
        daily = {}
        site = {}
        species = {}
        for members in self._resolved_components():
            representative_id = min(members)
            representative = self._observations[representative_id]
            contribution = max(self._observations[item]["count_value"] for item in members)
            date_key = representative["utc_time"].date().isoformat()
            site_key = representative["raw"]["latitude"] + "," + representative["raw"]["longitude"]
            species_key = representative["raw"]["species"]
            daily[date_key] = daily.get(date_key, 0) + contribution
            site[site_key] = site.get(site_key, 0) + contribution
            species[species_key] = species.get(species_key, 0) + contribution
        return {
            "daily": {key: daily[key] for key in sorted(daily)},
            "site": {key: site[key] for key in sorted(site)},
            "species": {key: species[key] for key in sorted(species)},
        }

    def canonical_export(self):
        observations = [
            dict(self._observations[item_id]["raw"])
            for item_id in sorted(self._observations)
        ]
        payload = {
            "observations": observations,
            "decisions": self._decision_list(),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def reconciliation_report(self):
        return {
            "groups": self.possible_duplicates(),
            "decisions": self._decision_list(),
        }

    def original_row(self, observation_id):
        if observation_id not in self._observations:
            raise KeyError(observation_id)
        observation = self._observations[observation_id]
        result = dict(observation["raw"])
        result["source"] = observation["source"]
        return result

    def _prepare_row(self, row, source):
        raw = {field: row[field] for field in _FIELDS}
        observation_id = raw["id"]
        if not observation_id:
            raise ValueError("observation IDs must be nonempty")
        try:
            local_time = datetime.fromisoformat(raw["local_time"])
        except (TypeError, ValueError) as error:
            raise ValueError("local_time must be a valid ISO-8601 timestamp") from error
        if local_time.tzinfo is None or local_time.utcoffset() is None:
            raise ValueError("local_time must contain an explicit UTC offset")
        try:
            latitude = float(raw["latitude"])
            longitude = float(raw["longitude"])
        except ValueError as error:
            raise ValueError("latitude and longitude must be numeric") from error
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise ValueError("latitude and longitude must be finite")
        try:
            count_value = int(raw["count"])
        except ValueError as error:
            raise ValueError("count must be an integer") from error
        return {
            "raw": raw,
            "source": source,
            "utc_time": local_time.astimezone(timezone.utc),
            "latitude_value": latitude,
            "longitude_value": longitude,
            "count_value": count_value,
        }

    def _commit(self, prepared):
        incoming_ids = [row["raw"]["id"] for row in prepared]
        if len(incoming_ids) != len(set(incoming_ids)):
            raise ValueError("observation IDs must be unique")
        if any(item_id in self._observations for item_id in incoming_ids):
            raise ValueError("observation IDs must be unique")
        for row in prepared:
            self._observations[row["raw"]["id"]] = row

    def _species_root(self, label):
        key = label.casefold()
        return self._alias_parent.get(key, key)

    def _species_compatible(self, left, right):
        return self._species_root(left) == self._species_root(right)

    def _near_pair(self, left, right):
        time_difference = abs((left["utc_time"] - right["utc_time"]).total_seconds())
        if time_difference > self.time_tolerance_seconds:
            return False
        latitude_difference = left["latitude_value"] - right["latitude_value"]
        longitude_difference = left["longitude_value"] - right["longitude_value"]
        distance = math.hypot(latitude_difference, longitude_difference)
        if distance > self.coordinate_tolerance:
            return False
        return self._species_compatible(left["raw"]["species"], right["raw"]["species"])

    def _candidate_groups(self):
        photo_buckets = {}
        for item_id, observation in self._observations.items():
            photo_hash = observation["raw"]["photo_hash"]
            if photo_hash:
                photo_buckets.setdefault(photo_hash, []).append(item_id)

        candidates = []
        for members in photo_buckets.values():
            if len(members) > 1:
                candidates.append((tuple(sorted(members)), "photo"))

        ids = sorted(self._observations)
        for index, left_id in enumerate(ids):
            left = self._observations[left_id]
            for right_id in ids[index + 1:]:
                right = self._observations[right_id]
                left_hash = left["raw"]["photo_hash"]
                right_hash = right["raw"]["photo_hash"]
                if left_hash and left_hash == right_hash:
                    continue
                if self._near_pair(left, right):
                    candidates.append(((left_id, right_id), "possible"))
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates

    def _group_dict(self, members, reason):
        digest = hashlib.sha256(",".join(members).encode("utf-8")).hexdigest()[:16]
        return {
            "group_id": digest,
            "members": list(members),
            "reason": reason,
        }

    def _decision_list(self):
        return [
            {"members": list(members), "decision": self._decisions[members]}
            for members in sorted(self._decisions)
        ]

    def _resolved_components(self):
        ids = sorted(self._observations)
        parent = {item_id: item_id for item_id in ids}

        def find(item_id):
            while parent[item_id] != item_id:
                parent[item_id] = parent[parent[item_id]]
                item_id = parent[item_id]
            return item_id

        def union(left, right):
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                if left_root < right_root:
                    parent[right_root] = left_root
                else:
                    parent[left_root] = right_root

        for members, reason in self._candidate_groups():
            if reason == "photo" and self._decisions.get(members) != "distinct":
                for member in members[1:]:
                    union(members[0], member)

        for members, decision in self._decisions.items():
            if decision == "same":
                for member in members[1:]:
                    union(members[0], member)

        components = {}
        for item_id in ids:
            components.setdefault(find(item_id), []).append(item_id)
        return [tuple(components[root]) for root in sorted(components)]
