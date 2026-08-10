"""Graph-constrained structural analogy: retrieve, map, then project.

This is a deliberately small, deterministic prototype of task #4834.  A frame
is a *situation with roles and relations*, not a noun or bag of attributes.

Pipeline
--------
1. ``cheap_shortlist`` uses only a retrieval signature (goal, constraints,
   failed predictions, domain).  It never reads relation graphs.  Domain is a
   small cost hint and the shortlist reserves cross-domain recall.
2. ``structural_match`` performs the expensive role-bijection search on the
   shortlist.  It aligns relation families and endpoint topology, and scores
   systematicity by connected relational depth.  Surface attributes do not
   contribute.
3. ``project_plan`` turns the selected map into requirements for the target
   plan.  Analogy remains a proposer: projected relations are questions to
   instantiate and test, not authoritative causal edges.

The module assumes relation graphs are already given.  It does not claim to
solve graph extraction from a novel situation; see the design note under
``docs/research/graph-constrained-analogy.md``.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ATTRIBUTE_KINDS = frozenset({"attribute", "property", "label"})


def _tokens(values: Iterable[str]) -> frozenset[str]:
    return frozenset(
        token
        for value in values
        for token in _TOKEN_RE.findall(str(value).lower())
    )


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / len(a | b) if a or b else 0.0


@dataclass(frozen=True)
class RetrievalSignature:
    """Cheap index fields.  Relation graphs are intentionally absent."""

    goal: tuple[str, ...]
    constraints: tuple[str, ...]
    failed_predictions: tuple[str, ...]
    domain: str


@dataclass(frozen=True)
class Relation:
    subject: str
    predicate: str
    object: str
    family: str
    kind: str = "relation"

    @property
    def is_structural(self) -> bool:
        return self.kind not in _ATTRIBUTE_KINDS


@dataclass(frozen=True)
class Situation:
    """A frame: situated roles plus observed and transferable relations."""

    id: str
    domain: str
    title: str
    summary: str
    signature: RetrievalSignature
    roles: Mapping[str, str]
    observed_relations: tuple[Relation, ...]
    transferable_relations: tuple[Relation, ...] = ()

    def __post_init__(self) -> None:
        endpoints = {
            endpoint
            for relation in (*self.observed_relations, *self.transferable_relations)
            for endpoint in (relation.subject, relation.object)
        }
        missing = endpoints - set(self.roles)
        if missing:
            raise ValueError(f"situation {self.id!r} has relations with unknown roles: {sorted(missing)}")


def situation_from_mapping(row: Mapping[str, Any]) -> Situation:
    """Build a situation from the portable JSON mapping contract.

    Unknown top-level and relation fields are ignored deliberately, so an
    extractor receipt may retain evidence spans and audit metadata without
    changing the deterministic matcher input.
    """
    signature = row["signature"]

    def relation(item: Mapping[str, Any]) -> Relation:
        return Relation(
            subject=str(item["subject"]),
            predicate=str(item["predicate"]),
            object=str(item["object"]),
            family=str(item["family"]),
            kind=str(item.get("kind", "relation")),
        )

    return Situation(
        id=str(row["id"]),
        domain=str(row["domain"]),
        title=str(row.get("title", "")),
        summary=str(row.get("summary", "")),
        signature=RetrievalSignature(
            goal=tuple(str(value) for value in signature.get("goal", ())),
            constraints=tuple(str(value) for value in signature.get("constraints", ())),
            failed_predictions=tuple(str(value) for value in signature.get("failed_predictions", ())),
            domain=str(signature["domain"]),
        ),
        roles={str(key): str(value) for key, value in row["roles"].items()},
        observed_relations=tuple(relation(item) for item in row["observed_relations"]),
        transferable_relations=tuple(relation(item) for item in row.get("transferable_relations", ())),
    )


def load_situation_file(path: str | Path) -> Situation:
    """Load one situation from JSON without a database or Apache AGE."""
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError("situation file must contain a JSON object")
    return situation_from_mapping(payload)


def load_situation_library_file(path: str | Path, *, key: str = "sources") -> tuple[Situation, ...]:
    """Load a situation array, or an array stored under ``key``, from JSON."""
    payload = json.loads(Path(path).read_text())
    rows = payload.get(key) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"situation library file must contain an array or an array at {key!r}")
    return tuple(situation_from_mapping(row) for row in rows)


@dataclass(frozen=True)
class ShortlistEntry:
    situation: Situation
    cheap_score: float
    field_scores: Mapping[str, float]


@dataclass(frozen=True)
class RelationCorrespondence:
    source: Relation
    target: Relation


@dataclass(frozen=True)
class StructuralCandidate:
    source: Situation
    role_mapping: Mapping[str, str]
    relation_correspondences: tuple[RelationCorrespondence, ...]
    matched_relation_count: int
    coverage: float
    largest_connected_component: int
    relational_depth: int
    systematicity_score: float
    accepted: bool
    rejection_reason: str | None = None


@dataclass(frozen=True)
class ProposedRole:
    target_role: str
    source_role: str
    source_description: str
    status: str = "needs_target_instantiation"


@dataclass(frozen=True)
class RequiredRelation:
    subject: str
    predicate: str
    object: str
    family: str
    source_relation: Relation


@dataclass(frozen=True)
class PlanProposal:
    target_id: str
    source_id: str
    role_mapping: Mapping[str, str]
    proposed_roles: tuple[ProposedRole, ...]
    required_relations: tuple[RequiredRelation, ...]
    authority: str = "advisory_hypothesis"


@dataclass(frozen=True)
class AnalogyResult:
    target: Situation
    shortlist: tuple[ShortlistEntry, ...]
    structural_candidates: tuple[StructuralCandidate, ...]
    selected: StructuralCandidate
    plan_proposal: PlanProposal


@dataclass(frozen=True)
class RetrievalRegimeComparison:
    """Frozen source choices for the required random/surface/structural control."""

    target_id: str
    random_source_id: str
    surface_source_id: str
    structural_source_id: str
    surface_ranking: tuple[tuple[str, float], ...]


def signature_score(target: RetrievalSignature, candidate: RetrievalSignature) -> tuple[float, dict[str, float]]:
    """Score cheap metadata only; domain is explicitly a low-weight cost hint."""
    fields = {
        "goal": _jaccard(target.goal, candidate.goal),
        "constraints": _jaccard(target.constraints, candidate.constraints),
        "failed_predictions": _jaccard(target.failed_predictions, candidate.failed_predictions),
        "domain_cost_hint": 1.0 if target.domain == candidate.domain else 0.0,
    }
    score = (
        0.42 * fields["goal"]
        + 0.28 * fields["constraints"]
        + 0.22 * fields["failed_predictions"]
        + 0.08 * fields["domain_cost_hint"]
    )
    return score, fields


def cheap_shortlist(
    signature: RetrievalSignature,
    library: Sequence[Situation],
    *,
    limit: int = 5,
    cross_domain_reserve: int = 1,
) -> tuple[ShortlistEntry, ...]:
    """Stage 1: pull a small candidate set without inspecting any graphs.

    The normal score may prefer a same-domain case because it is cheaper to use.
    ``cross_domain_reserve`` prevents that cost hint from becoming a relevance
    gate.  It does not grant cross-domain sources any stage-2 score.
    """
    if limit < 1:
        raise ValueError("shortlist limit must be >= 1")
    scored = []
    for situation in library:
        score, fields = signature_score(signature, situation.signature)
        scored.append(ShortlistEntry(situation, round(score, 8), fields))
    scored.sort(key=lambda row: (-row.cheap_score, row.situation.id))
    selected = list(scored[:limit])

    reserve = min(cross_domain_reserve, limit)
    present_cross = sum(row.situation.domain != signature.domain for row in selected)
    if present_cross < reserve:
        cross = [row for row in scored if row.situation.domain != signature.domain and row not in selected]
        for replacement in cross[: reserve - present_cross]:
            # Replace the weakest same-domain entry.  Stage 2, not domain, will
            # decide whether the reserved candidate is relevant.
            replace_at = next(
                (idx for idx in range(len(selected) - 1, -1, -1)
                 if selected[idx].situation.domain == signature.domain),
                None,
            )
            if replace_at is None:
                break
            selected[replace_at] = replacement
    selected.sort(key=lambda row: (-row.cheap_score, row.situation.id))
    return tuple(selected)


def _structural_relations(situation: Situation) -> tuple[Relation, ...]:
    return tuple(relation for relation in situation.observed_relations if relation.is_structural)


def _best_correspondences(
    source_relations: Sequence[Relation],
    target_relations: Sequence[Relation],
    mapping: Mapping[str, str],
) -> tuple[RelationCorrespondence, ...]:
    """Maximum deterministic one-to-one relation alignment for a fixed role map."""
    compatible: list[tuple[int, int]] = []
    for source_idx, source in enumerate(source_relations):
        for target_idx, target in enumerate(target_relations):
            if (
                source.family == target.family
                and mapping.get(source.subject) == target.subject
                and mapping.get(source.object) == target.object
            ):
                compatible.append((source_idx, target_idx))

    best: tuple[tuple[int, int], ...] = ()

    def visit(index: int, chosen: tuple[tuple[int, int], ...], used_s: set[int], used_t: set[int]) -> None:
        nonlocal best
        if index == len(compatible):
            if len(chosen) > len(best) or (len(chosen) == len(best) and chosen < best):
                best = chosen
            return
        source_idx, target_idx = compatible[index]
        visit(index + 1, chosen, used_s, used_t)
        if source_idx not in used_s and target_idx not in used_t:
            visit(
                index + 1,
                chosen + ((source_idx, target_idx),),
                used_s | {source_idx},
                used_t | {target_idx},
            )

    visit(0, (), set(), set())
    return tuple(
        RelationCorrespondence(source_relations[source_idx], target_relations[target_idx])
        for source_idx, target_idx in best
    )


def _component_edge_counts(relations: Sequence[Relation]) -> list[int]:
    if not relations:
        return []
    adjacency: dict[str, set[str]] = {}
    for relation in relations:
        adjacency.setdefault(relation.subject, set()).add(relation.object)
        adjacency.setdefault(relation.object, set()).add(relation.subject)
    components: list[set[str]] = []
    unseen = set(adjacency)
    while unseen:
        root = min(unseen)
        stack, nodes = [root], set()
        while stack:
            node = stack.pop()
            if node in nodes:
                continue
            nodes.add(node)
            unseen.discard(node)
            stack.extend(sorted(adjacency[node] - nodes, reverse=True))
        components.append(nodes)
    return [
        sum(1 for relation in relations if relation.subject in nodes and relation.object in nodes)
        for nodes in components
    ]


def _relational_depth(relations: Sequence[Relation]) -> int:
    """Longest simple undirected path, exact for intentionally small shortlist graphs."""
    adjacency: dict[str, set[str]] = {}
    for relation in relations:
        adjacency.setdefault(relation.subject, set()).add(relation.object)
        adjacency.setdefault(relation.object, set()).add(relation.subject)

    def walk(node: str, visited: frozenset[str]) -> int:
        return max(
            (1 + walk(nxt, visited | {nxt}) for nxt in adjacency.get(node, ()) if nxt not in visited),
            default=0,
        )

    return max((walk(node, frozenset({node})) for node in adjacency), default=0)


def structural_match(source: Situation, target: Situation) -> StructuralCandidate:
    """Stage 2: return the best role map under relational systematicity.

    Relation predicate wording may differ.  A correspondence requires the same
    relation *family* and preserved directed endpoints under a one-to-one role
    map.  Independent attribute/property rows are excluded completely.
    """
    source_relations = _structural_relations(source)
    target_relations = _structural_relations(target)
    source_roles = sorted({r.subject for r in source_relations} | {r.object for r in source_relations})
    target_roles = sorted({r.subject for r in target_relations} | {r.object for r in target_relations})

    if len(source_roles) > len(target_roles) or not source_roles or not target_roles:
        return StructuralCandidate(source, {}, (), 0, 0.0, 0, 0, 0.0, False, "role cardinality cannot align")

    best_key: tuple[float, int, int, int, tuple[str, ...]] | None = None
    best_payload: tuple[dict[str, str], tuple[RelationCorrespondence, ...], float, int, int, float] | None = None
    for assigned in itertools.permutations(target_roles, len(source_roles)):
        mapping = dict(zip(source_roles, assigned))
        correspondences = _best_correspondences(source_relations, target_relations, mapping)
        matched = [row.source for row in correspondences]
        count = len(correspondences)
        coverage = count / max(len(source_relations), len(target_relations), 1)
        components = _component_edge_counts(matched)
        largest = max(components, default=0)
        depth = _relational_depth(matched)
        connected_fraction = largest / count if count else 0.0
        depth_fraction = depth / max(len(matched), 1)
        # Systematicity: a connected chain earns more than the same number of
        # isolated correspondences. Coverage remains load-bearing.
        score = coverage + 0.5 * connected_fraction + 0.25 * depth_fraction
        key = (score, count, largest, depth, tuple(reversed(assigned)))
        if best_key is None or key > best_key:
            best_key = key
            best_payload = mapping, correspondences, coverage, largest, depth, score

    assert best_payload is not None
    mapping, correspondences, coverage, largest, depth, score = best_payload
    count = len(correspondences)
    accepted = count >= 2 and largest >= 2 and coverage >= 0.5
    if count < 2:
        reason = "fewer than two relational correspondences"
    elif largest < 2:
        reason = "matches are independent rather than a connected system"
    elif coverage < 0.5:
        reason = "connected match covers less than half of the compared structure"
    else:
        reason = None
    return StructuralCandidate(
        source=source,
        role_mapping=mapping,
        relation_correspondences=correspondences,
        matched_relation_count=count,
        coverage=round(coverage, 8),
        largest_connected_component=largest,
        relational_depth=depth,
        systematicity_score=round(score, 8),
        accepted=accepted,
        rejection_reason=reason,
    )


def project_plan(target: Situation, candidate: StructuralCandidate) -> PlanProposal:
    """Stage 3: project only post-retrieval source relations into target roles."""
    if not candidate.accepted:
        raise ValueError("cannot project a rejected structural match")
    mapping = dict(candidate.role_mapping)
    proposed: dict[str, ProposedRole] = {}

    def endpoint(source_role: str) -> str:
        if source_role in mapping:
            return mapping[source_role]
        target_role = f"proposed_{source_role}"
        proposed.setdefault(
            target_role,
            ProposedRole(target_role, source_role, candidate.source.roles[source_role]),
        )
        return target_role

    requirements = tuple(
        RequiredRelation(
            subject=endpoint(relation.subject),
            predicate=relation.predicate,
            object=endpoint(relation.object),
            family=relation.family,
            source_relation=relation,
        )
        for relation in candidate.source.transferable_relations
    )
    return PlanProposal(
        target_id=target.id,
        source_id=candidate.source.id,
        role_mapping=mapping,
        proposed_roles=tuple(proposed[key] for key in sorted(proposed)),
        required_relations=requirements,
    )


def retrieve_analogy(
    target: Situation,
    library: Sequence[Situation],
    *,
    shortlist_size: int = 5,
    cross_domain_reserve: int = 1,
) -> AnalogyResult:
    shortlist = cheap_shortlist(
        target.signature,
        library,
        limit=min(shortlist_size, len(library)),
        cross_domain_reserve=cross_domain_reserve,
    )
    candidates = tuple(structural_match(row.situation, target) for row in shortlist)
    accepted = [row for row in candidates if row.accepted]
    if not accepted:
        raise LookupError(f"no accepted structural analogue for {target.id}")
    accepted.sort(
        key=lambda row: (
            -row.systematicity_score,
            -row.matched_relation_count,
            row.source.id,
        )
    )
    selected = accepted[0]
    return AnalogyResult(target, shortlist, candidates, selected, project_plan(target, selected))


def _surface_score(target: Situation, source: Situation) -> float:
    target_text = (target.title, target.summary, target.domain)
    source_text = (source.title, source.summary, source.domain)
    lexical = _jaccard(target_text, source_text)
    return lexical + (0.15 if target.domain == source.domain else 0.0)


def compare_retrieval_regimes(
    target: Situation,
    library: Sequence[Situation],
    *,
    shortlist_size: int = 5,
    random_seed: int = 4834,
) -> RetrievalRegimeComparison:
    """Choose sources under random, surface, and structural regimes.

    This freezes retrieval choices only.  A larger evaluation must give each
    source to the same downstream planner and score target outcomes blindly.
    """
    if not library:
        raise ValueError("library must not be empty")
    random_source = random.Random(random_seed).choice(list(library))
    surface = sorted(
        ((source.id, round(_surface_score(target, source), 8)) for source in library),
        key=lambda row: (-row[1], row[0]),
    )
    structural = retrieve_analogy(target, library, shortlist_size=shortlist_size)
    return RetrievalRegimeComparison(
        target_id=target.id,
        random_source_id=random_source.id,
        surface_source_id=surface[0][0],
        structural_source_id=structural.selected.source.id,
        surface_ranking=tuple(surface),
    )
