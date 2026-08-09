import csv
import datetime
import hashlib
import io
import json
import math


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
_REQUIRED_FIELDS = _FIELDS[:-2]


class Survey:
    def __init__(self, time_tolerance_seconds, coordinate_tolerance):
        self.time_tolerance_seconds = float(time_tolerance_seconds)
        self.coordinate_tolerance = float(coordinate_tolerance)
        if not math.isfinite(self.time_tolerance_seconds) or self.time_tolerance_seconds < 0:
            raise ValueError("time_tolerance_seconds must be finite and nonnegative")
        if not math.isfinite(self.coordinate_tolerance) or self.coordinate_tolerance < 0:
            raise ValueError("coordinate_tolerance must be finite and nonnegative")

        self._observations = {}
        self._decisions = {}
        self._alias_component = {}

    def set_aliases(self, mapping):
        if not isinstance(mapping, dict):
            raise TypeError("mapping must be a dict")

        graph = {}
        for key, values in mapping.items():
            if isinstance(values, (str, bytes)):
                raise TypeError("each alias value must be a list of spellings")
            try:
                spellings = [str(key)] + [str(value) for value in values]
            except TypeError as exc:
                raise TypeError("each alias value must be iterable") from exc

            normalized = {spelling.casefold() for spelling in spellings}
            for spelling in normalized:
                graph.setdefault(spelling, set()).update(normalized - {spelling})

        components = {}
        visited = set()
        component_number = 0
        for start in sorted(graph):
            if start in visited:
                continue
            stack = [start]
            visited.add(start)
            while stack:
                current = stack.pop()
                components[current] = component_number
                for neighbor in graph[current]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)
            component_number += 1

        self._alias_component = components

    def import_csv(self, text):
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames is None:
            raise ValueError("CSV input must contain a header")
        missing = [field for field in _FIELDS if field not in reader.fieldnames]
        if missing:
            raise ValueError("missing CSV columns: " + ",".join(missing))

        prepared = []
        for row in reader:
            raw = {field: self._csv_string(row.get(field)) for field in _FIELDS}
            prepared.append(self._prepare(raw, "csv"))
        self._commit(prepared)

    def import_json(self, rows):
        prepared = []
        for row in rows:
            if not isinstance(row, dict):
                raise TypeError("each JSON observation must be a dict")
            missing = [field for field in _REQUIRED_FIELDS if field not in row]
            if missing:
                raise ValueError("missing JSON fields: " + ",".join(missing))
            raw = {
                field: self._json_string(row.get(field, ""))
                for field in _FIELDS
            }
            prepared.append(self._prepare(raw, "json"))
        self._commit(prepared)

    def possible_duplicates(self):
        groups = []
        for members in self._candidate_components():
            if members in self._decisions:
                continue
            hashes = {self._observations[item]["raw"]["photo_hash"] for item in members}
            reason = "photo" if len(hashes) == 1 and "" not in hashes else "possible"
            groups.append({
                "group_id": self._group_id(members),
                "members": list(members),
                "reason": reason,
            })
        groups.sort(key=lambda group: tuple(group["members"]))
        return groups

    def decide(self, member_ids, decision):
        if decision not in ("same", "distinct"):
            raise ValueError("decision must be 'same' or 'distinct'")

        supplied = list(member_ids)
        if len(supplied) != len(set(supplied)):
            raise ValueError("member_ids must not contain duplicates")
        members = tuple(sorted(supplied))
        current = {
            tuple(group["members"])
            for group in self.possible_duplicates()
        }
        if members not in current:
            raise ValueError("member_ids must exactly match a currently reported group")

        proposed = dict(self._decisions)
        proposed[members] = decision
        self._validate_explicit_decisions(proposed)
        self._decisions = proposed

    def summaries(self):
        daily = {}
        site = {}
        species = {}

        for members in self._resolved_components():
            representative_id = min(members)
            representative = self._observations[representative_id]
            count = max(self._observations[item]["count"] for item in members)

            daily_key = representative["utc"].date().isoformat()
            raw = representative["raw"]
            site_key = raw["latitude"] + "," + raw["longitude"]
            species_key = raw["species"]

            daily[daily_key] = daily.get(daily_key, 0) + count
            site[site_key] = site.get(site_key, 0) + count
            species[species_key] = species.get(species_key, 0) + count

        return {
            "daily": dict(sorted(daily.items())),
            "site": dict(sorted(site.items())),
            "species": dict(sorted(species.items())),
        }

    def canonical_export(self):
        observations = []
        for observation_id in sorted(self._observations):
            observations.append(dict(self._observations[observation_id]["original"]))

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
        try:
            return dict(self._observations[observation_id]["original"])
        except KeyError as exc:
            raise KeyError(observation_id) from exc

    @staticmethod
    def _csv_string(value):
        return "" if value is None else value

    @staticmethod
    def _json_string(value):
        return "" if value is None else str(value)

    def _prepare(self, raw, source):
        observation_id = raw["id"]
        if not observation_id:
            raise ValueError("observation IDs must be nonempty")

        local_time = raw["local_time"]
        try:
            parsed_time = datetime.datetime.fromisoformat(local_time)
        except (TypeError, ValueError) as exc:
            raise ValueError("local_time must be a valid ISO-8601 datetime") from exc
        if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
            raise ValueError("local_time must contain an explicit UTC offset")
        utc_time = parsed_time.astimezone(datetime.timezone.utc)

        try:
            latitude = float(raw["latitude"])
            longitude = float(raw["longitude"])
        except (TypeError, ValueError) as exc:
            raise ValueError("latitude and longitude must be numeric") from exc
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise ValueError("latitude and longitude must be finite")

        try:
            count = int(raw["count"])
        except (TypeError, ValueError) as exc:
            raise ValueError("count must be an integer") from exc

        original = dict(raw)
        original["source"] = source
        return {
            "id": observation_id,
            "raw": dict(raw),
            "original": original,
            "utc": utc_time,
            "latitude": latitude,
            "longitude": longitude,
            "count": count,
        }

    def _commit(self, prepared):
        seen = set(self._observations)
        for observation in prepared:
            observation_id = observation["id"]
            if observation_id in seen:
                raise ValueError("observation IDs must be unique")
            seen.add(observation_id)
        for observation in prepared:
            self._observations[observation["id"]] = observation

    def _species_compatible(self, left, right):
        left_normalized = left.casefold()
        right_normalized = right.casefold()
        if left_normalized == right_normalized:
            return True
        left_component = self._alias_component.get(left_normalized)
        return (
            left_component is not None
            and left_component == self._alias_component.get(right_normalized)
        )

    def _pair_reason(self, left, right):
        left_raw = left["raw"]
        right_raw = right["raw"]
        left_hash = left_raw["photo_hash"]
        if left_hash and left_hash == right_raw["photo_hash"]:
            return "photo"

        elapsed = abs((left["utc"] - right["utc"]).total_seconds())
        if elapsed > self.time_tolerance_seconds:
            return None

        latitude_delta = left["latitude"] - right["latitude"]
        longitude_delta = left["longitude"] - right["longitude"]
        distance = math.hypot(latitude_delta, longitude_delta)
        if distance > self.coordinate_tolerance:
            return None

        if not self._species_compatible(left_raw["species"], right_raw["species"]):
            return None
        return "possible"

    def _candidate_components(self):
        ids = sorted(self._observations)
        parent = {item: item for item in ids}

        def find(item):
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        def union(left, right):
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                if left_root < right_root:
                    parent[right_root] = left_root
                else:
                    parent[left_root] = right_root

        involved = set()
        for index, left_id in enumerate(ids):
            for right_id in ids[index + 1:]:
                if self._pair_reason(
                    self._observations[left_id],
                    self._observations[right_id],
                ) is not None:
                    union(left_id, right_id)
                    involved.add(left_id)
                    involved.add(right_id)

        components = {}
        for item in involved:
            components.setdefault(find(item), []).append(item)
        return sorted(
            (tuple(sorted(members)) for members in components.values()),
            key=lambda members: members,
        )

    def _validate_explicit_decisions(self, decisions):
        ids = sorted(self._observations)
        parent = {item: item for item in ids}

        def find(item):
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        def union(left, right):
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for members, decision in sorted(decisions.items()):
            if decision == "same":
                for item in members[1:]:
                    union(members[0], item)

        for members, decision in decisions.items():
            if decision != "distinct":
                continue
            for index, left in enumerate(members):
                for right in members[index + 1:]:
                    if find(left) == find(right):
                        raise ValueError("decision conflicts with a persisted decision")

    def _resolved_components(self):
        ids = sorted(self._observations)
        parent = {item: item for item in ids}

        distinct_pairs = set()
        for members, decision in self._decisions.items():
            if decision == "distinct":
                for index, left in enumerate(members):
                    for right in members[index + 1:]:
                        distinct_pairs.add((left, right))

        def find(item):
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        def can_union(left, right):
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                return True
            for distinct_left, distinct_right in distinct_pairs:
                roots = {find(distinct_left), find(distinct_right)}
                if roots == {left_root, right_root}:
                    return False
            return True

        def union(left, right, constrained=False):
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                return
            if constrained and not can_union(left, right):
                return
            if left_root < right_root:
                parent[right_root] = left_root
            else:
                parent[left_root] = right_root

        for members, decision in sorted(self._decisions.items()):
            if decision == "same":
                for item in members[1:]:
                    union(members[0], item)

        hashes = {}
        for item in ids:
            photo_hash = self._observations[item]["raw"]["photo_hash"]
            if photo_hash:
                hashes.setdefault(photo_hash, []).append(item)
        for photo_hash in sorted(hashes):
            members = sorted(hashes[photo_hash])
            for index, left in enumerate(members):
                for right in members[index + 1:]:
                    union(left, right, constrained=True)

        components = {}
        for item in ids:
            components.setdefault(find(item), []).append(item)
        return sorted(
            (tuple(sorted(members)) for members in components.values()),
            key=lambda members: members,
        )

    @staticmethod
    def _group_id(members):
        joined = ",".join(members).encode("utf-8")
        return hashlib.sha256(joined).hexdigest()[:16]


__all__ = ["Survey"]
