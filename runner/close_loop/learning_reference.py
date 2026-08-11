"""Learning-reference propagation, retrieval, and grounding verification.

This is the *grounding anchor* of the close-the-loop grounded-evaluator gate
(``docs/design/close-loop-grounded-evaluator-gate.md``).  It closes the one wire
the design names: a close-the-loop work product must carry a **learning
reference** — typed links to the specific prior experiences (corrections,
reference events, grounded principles) that generated the change — so the
evaluator's strongest check has something to retrieve.

Two halves live here, and only these two.  This module does **not** contain the
LLM judge (that is the evaluator, built separately), and it does not arm or wire
anything into the live loop.

1. **Propagation.**  :class:`LearningReference` is the durable, structured
   reference record emitted onto a work product.  It reuses the correction /
   :class:`~runner.consultants.self_correction.ReferenceEvent` vocabulary of the
   self-correction consultant and the canonical-digest machinery of
   :mod:`runner.close_loop.hashing`.  It is made **tamper-evident** by binding
   its digest into the task's existing intent/lineage integrity: the
   :meth:`LearningReference.lineage_hash` domain-separates the intent hash and
   the reference digest, and the same payload can travel as a git commit trailer
   (:meth:`commit_trailer_lines` / :func:`parse_commit_trailers`) so the git
   object also witnesses it.  Altering a citation after emission changes the
   digest and breaks the lineage hash.

2. **Retrieval + verification.**  :func:`verify_learning_reference` takes a work
   product's reference and an :class:`ExperienceStore` — the system's own record,
   which the candidate cannot author — and checks, per citation, that the cited
   experience (i) **exists**, (ii) is owned by a principal **other than** the
   candidate (the governance separate-principal model: the candidate did not and
   cannot author it), and (iii) matches the provenance the citation claims.  A
   dangling citation or one pointing at candidate-authored / same-principal data
   is rejected as ungrounded.  The verified experiences are returned for the
   evaluator to judge; this library renders *no* semantic verdict on them.

Non-learning changes legitimately carry an empty reference; that is reported as
:class:`GroundingVerdict.NO_REFERENCE` ("no grounding to check"), never an error,
per the design's fallback.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .hashing import canonical_digest, canonical_json
from runner.consultants.self_correction import ReferenceEvent, ReferenceKind

LEARNING_REFERENCE_DOMAIN = "aq-close-loop/learning-reference/v1"
LINEAGE_DOMAIN = "aq-close-loop/learning-lineage/v1"

# Git trailer keys.  A trailer is ``Key: value`` at the foot of a commit message;
# these three carry the reference so the immutable git object also witnesses it.
TRAILER_PAYLOAD = "Learning-Reference"
TRAILER_DIGEST = "Learning-Reference-Digest"
TRAILER_LINEAGE = "Learning-Lineage"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TRAILER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):[ \t]*(.*)$")


class ReferenceType(StrEnum):
    """The kind of prior experience a citation points at.

    These are the three generating-experience records the design names.  The
    value is stable wire text; an unknown value fails construction rather than
    silently broadening what counts as grounding.
    """

    CORRECTION = "correction"
    REFERENCE_EVENT = "reference_event"
    GROUNDED_PRINCIPLE = "grounded_principle"

    @classmethod
    def parse(cls, value: "ReferenceType | str") -> "ReferenceType":
        if isinstance(value, cls):
            return value
        if type(value) is not str or value not in {member.value for member in cls}:
            raise ValueError(f"invalid learning-reference type: {value!r}")
        return cls(value)


class LearningReferenceError(ValueError):
    """A malformed or ambiguous learning reference; fail closed rather than guess."""


@dataclass(frozen=True, slots=True)
class ExperienceCitation:
    """One typed link from a work product to a generating experience.

    ``owning_principal`` is the provenance the *worker claims* for the cited
    record.  It is deliberately verified against the store's authoritative owner
    (:func:`verify_learning_reference`): a candidate that lies about who owns an
    experience is caught, and a citation is grounded only when the store agrees.
    """

    reference_type: ReferenceType
    reference_id: str
    owning_principal: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_type", ReferenceType.parse(self.reference_type))
        if not isinstance(self.reference_id, str) or not self.reference_id.strip():
            raise LearningReferenceError("citation reference_id must be a non-empty string")
        if not isinstance(self.owning_principal, str) or not self.owning_principal.strip():
            raise LearningReferenceError("citation owning_principal must be a non-empty string")

    @property
    def key(self) -> tuple[ReferenceType, str]:
        return (self.reference_type, self.reference_id)

    def payload(self) -> dict[str, str]:
        return {
            "reference_type": self.reference_type.value,
            "reference_id": self.reference_id,
            "owning_principal": self.owning_principal,
        }

    @classmethod
    def for_reference_event(
        cls, reference_id: str, owning_principal: str, event: ReferenceEvent
    ) -> "ExperienceCitation":
        """Build a citation for a self-correction :class:`ReferenceEvent`.

        The event content is not copied into the citation — the store owns the
        authoritative record — but this keeps the reuse explicit and rejects an
        event that carries no semantic rule identity to ground against.
        """
        if not isinstance(event, ReferenceEvent):
            raise LearningReferenceError("event must be a ReferenceEvent")
        if not event.semantic_rule_identity.strip():
            raise LearningReferenceError("reference event has no generating rule identity")
        return cls(ReferenceType.REFERENCE_EVENT, reference_id, owning_principal)


@dataclass(frozen=True, slots=True)
class LearningReference:
    """The durable, tamper-evident reference record emitted onto a work product.

    ``candidate_principal`` is the worker/candidate that produced the change; it
    is recorded here so grounding verification can enforce that no cited
    experience is owned by that same principal.  Citations are de-duplicated by
    ``(type, id)`` and frozen into a stable, canonically hashable payload.
    """

    candidate_principal: str
    citations: tuple[ExperienceCitation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_principal, str) or not self.candidate_principal.strip():
            raise LearningReferenceError("candidate_principal must be a non-empty string")
        normalized: list[ExperienceCitation] = []
        seen: set[tuple[ReferenceType, str]] = set()
        for citation in self.citations:
            if not isinstance(citation, ExperienceCitation):
                raise LearningReferenceError("citations must be ExperienceCitation instances")
            if citation.key in seen:
                raise LearningReferenceError(
                    f"duplicate citation {citation.reference_type.value}:{citation.reference_id}"
                )
            seen.add(citation.key)
            normalized.append(citation)
        # Canonical order makes the digest independent of authoring order.
        normalized.sort(key=lambda c: (c.reference_type.value, c.reference_id))
        object.__setattr__(self, "citations", tuple(normalized))

    @property
    def is_empty(self) -> bool:
        """True for an ordinary non-learning change (no grounding to check)."""
        return not self.citations

    def payload(self) -> dict[str, Any]:
        return {
            "schema": LEARNING_REFERENCE_DOMAIN,
            "candidate_principal": self.candidate_principal,
            "citations": [citation.payload() for citation in self.citations],
        }

    @property
    def reference_digest(self) -> str:
        """Domain-separated digest over the canonical reference payload."""
        return canonical_digest(LEARNING_REFERENCE_DOMAIN, self.payload())

    def lineage_hash(self, intent_hash: str) -> str:
        """Bind the reference digest into the task's intent/lineage integrity.

        The result changes if either the admitted intent hash or any citation
        changes, so a silently altered reference no longer matches the lineage
        hash recorded on the work row / in the commit trailer.
        """
        if not isinstance(intent_hash, str) or not _HEX64.fullmatch(intent_hash):
            raise LearningReferenceError("intent_hash must be a lowercase sha256 hex digest")
        return canonical_digest(
            LINEAGE_DOMAIN,
            {"intent_hash": intent_hash, "reference_digest": self.reference_digest},
        )

    def commit_trailer_lines(self, intent_hash: str) -> tuple[str, ...]:
        """Render the reference as git commit trailers.

        The payload rides as base64 canonical JSON (a commit trailer value is a
        single line), beside its digest and the lineage hash.  A verifier
        reconstructs the reference from the payload and recomputes both hashes;
        an edit to any trailer breaks the recomputation.
        """
        encoded = base64.b64encode(canonical_json(self.payload()).encode("utf-8")).decode("ascii")
        return (
            f"{TRAILER_PAYLOAD}: {encoded}",
            f"{TRAILER_DIGEST}: {self.reference_digest}",
            f"{TRAILER_LINEAGE}: {self.lineage_hash(intent_hash)}",
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "LearningReference":
        """Reconstruct a reference from its canonical payload, validating shape."""
        if not isinstance(payload, Mapping):
            raise LearningReferenceError("learning reference payload must be an object")
        if payload.get("schema") != LEARNING_REFERENCE_DOMAIN:
            raise LearningReferenceError("learning reference payload has an unknown schema")
        candidate = payload.get("candidate_principal")
        raw_citations = payload.get("citations")
        if not isinstance(raw_citations, Sequence) or isinstance(raw_citations, (str, bytes)):
            raise LearningReferenceError("learning reference citations must be a list")
        citations: list[ExperienceCitation] = []
        for item in raw_citations:
            if not isinstance(item, Mapping) or set(item) != {
                "reference_type", "reference_id", "owning_principal"
            }:
                raise LearningReferenceError("citation payload has missing or unknown fields")
            citations.append(
                ExperienceCitation(
                    ReferenceType.parse(item["reference_type"]),
                    str(item["reference_id"]),
                    str(item["owning_principal"]),
                )
            )
        return cls(str(candidate), tuple(citations))


@dataclass(frozen=True, slots=True)
class ParsedTrailers:
    """A learning reference recovered from a commit message, with its claims."""

    reference: LearningReference
    declared_digest: str | None
    declared_lineage: str | None


def parse_commit_trailers(message: str) -> ParsedTrailers | None:
    """Recover a learning reference from a commit message's trailer block.

    Returns ``None`` when the message carries no learning-reference payload (an
    ordinary non-learning commit).  Raises on a present-but-malformed payload so
    a corrupted trailer fails closed rather than reading as "no reference".
    """
    if not isinstance(message, str):
        raise LearningReferenceError("commit message must be a string")
    trailers: dict[str, str] = {}
    for line in message.splitlines():
        match = _TRAILER_LINE.match(line)
        if match:
            trailers[match.group(1)] = match.group(2).strip()
    encoded = trailers.get(TRAILER_PAYLOAD)
    if encoded is None:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LearningReferenceError("learning reference trailer is not valid base64") from exc
    try:
        import json

        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LearningReferenceError("learning reference trailer is not valid JSON") from exc
    reference = LearningReference.from_payload(payload)
    return ParsedTrailers(reference, trailers.get(TRAILER_DIGEST), trailers.get(TRAILER_LINEAGE))


def verify_lineage_integrity(
    reference: LearningReference, intent_hash: str, expected_lineage_hash: str
) -> bool:
    """True only if the reference still hashes to the durably recorded lineage.

    This is the tamper check: ``expected_lineage_hash`` is the value stored on the
    work row / task_work_link (or the commit trailer) at emission time.  Altering
    any citation changes :meth:`LearningReference.reference_digest` and therefore
    the recomputed lineage hash, so the comparison fails.
    """
    if not isinstance(expected_lineage_hash, str) or not _HEX64.fullmatch(expected_lineage_hash):
        return False
    return reference.lineage_hash(intent_hash) == expected_lineage_hash


# --------------------------------------------------------------------------- #
# Retrieval + verification                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExperienceRecord:
    """The system's authoritative record for one cited experience.

    ``owning_principal`` is the store's own answer to "who wrote this", never the
    candidate's claim.  This is the value the separate-principal check trusts.
    """

    reference_type: ReferenceType
    reference_id: str
    owning_principal: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_type", ReferenceType.parse(self.reference_type))
        if not isinstance(self.reference_id, str) or not self.reference_id.strip():
            raise LearningReferenceError("experience reference_id must be a non-empty string")
        if not isinstance(self.owning_principal, str) or not self.owning_principal.strip():
            raise LearningReferenceError("experience owning_principal must be a non-empty string")


@runtime_checkable
class ExperienceStore(Protocol):
    """The system's own record of prior experiences, which the candidate cannot author.

    The real implementation reads the grounding ledger (``schema/022``,
    ``schema/025``) and correction/reference-event records under a principal the
    candidate does not control.  :class:`InMemoryExperienceStore` is the test and
    reference implementation.  ``fetch`` returns ``None`` for a citation that does
    not resolve — that dangling case is rejected by the verifier.
    """

    def fetch(
        self, reference_type: ReferenceType, reference_id: str
    ) -> ExperienceRecord | None:
        ...


class InMemoryExperienceStore:
    """A simple, inspectable :class:`ExperienceStore` for tests and reference use."""

    def __init__(self, records: Sequence[ExperienceRecord] = ()) -> None:
        self._records: dict[tuple[ReferenceType, str], ExperienceRecord] = {}
        for record in records:
            self.add(record)

    def add(self, record: ExperienceRecord) -> None:
        if not isinstance(record, ExperienceRecord):
            raise LearningReferenceError("store records must be ExperienceRecord instances")
        key = (record.reference_type, record.reference_id)
        if key in self._records:
            raise LearningReferenceError(
                f"duplicate experience record {record.reference_type.value}:{record.reference_id}"
            )
        self._records[key] = record

    def fetch(
        self, reference_type: ReferenceType, reference_id: str
    ) -> ExperienceRecord | None:
        return self._records.get((ReferenceType.parse(reference_type), reference_id))


class CitationStatus(StrEnum):
    """The outcome of verifying a single citation against the store."""

    GROUNDED = "grounded"
    DANGLING = "dangling"                    # (i) does not exist in the record
    SAME_PRINCIPAL = "same_principal"        # (ii) owned by the candidate itself
    CLAIMED_OWNER_MISMATCH = "claimed_owner_mismatch"  # (iii) provenance lie


class GroundingVerdict(StrEnum):
    """The overall grounding outcome for a work product's reference."""

    GROUNDED = "grounded"          # every citation resolved, independent, honest
    UNGROUNDED = "ungrounded"      # at least one citation was rejected
    NO_REFERENCE = "no_reference"  # ordinary non-learning change; nothing to check


@dataclass(frozen=True, slots=True)
class CitationVerification:
    """Per-citation verdict, carrying the retrieved record when grounded."""

    citation: ExperienceCitation
    status: CitationStatus
    record: ExperienceRecord | None = None

    @property
    def grounded(self) -> bool:
        return self.status is CitationStatus.GROUNDED


@dataclass(frozen=True, slots=True)
class GroundingVerification:
    """The whole-reference grounding decision handed to the evaluator.

    :attr:`grounded_experiences` are the retrieved, independently-owned records
    for the evaluator to judge semantically.  This library does not judge them.
    """

    verdict: GroundingVerdict
    candidate_principal: str
    citations: tuple[CitationVerification, ...]

    @property
    def is_grounded(self) -> bool:
        return self.verdict is GroundingVerdict.GROUNDED

    @property
    def has_reference(self) -> bool:
        return self.verdict is not GroundingVerdict.NO_REFERENCE

    @property
    def grounded_experiences(self) -> tuple[ExperienceRecord, ...]:
        return tuple(
            item.record for item in self.citations
            if item.status is CitationStatus.GROUNDED and item.record is not None
        )

    @property
    def rejected(self) -> tuple[CitationVerification, ...]:
        return tuple(item for item in self.citations if not item.grounded)

    def reason_codes(self) -> tuple[str, ...]:
        """Stable, de-duplicated rejection reasons for logging and gate wiring."""
        return tuple(dict.fromkeys(
            f"{item.status.value}:{item.citation.reference_type.value}:{item.citation.reference_id}"
            for item in self.citations if not item.grounded
        ))


def verify_learning_reference(
    reference: LearningReference, store: ExperienceStore
) -> GroundingVerification:
    """Retrieve and verify every cited experience against the system's record.

    Fail-closed, per citation:

    * **exists** — a citation the store cannot resolve is ``DANGLING``;
    * **separate principal** — a citation whose authoritative owner is the
      candidate is ``SAME_PRINCIPAL`` (the candidate cannot ground a learning in
      its own record);
    * **honest provenance** — a citation whose claimed ``owning_principal``
      disagrees with the store's authoritative owner is ``CLAIMED_OWNER_MISMATCH``.

    An empty reference is :class:`GroundingVerdict.NO_REFERENCE` — the design's
    fallback for ordinary non-learning changes, not an error.  The overall
    verdict is ``GROUNDED`` only when every citation is grounded.
    """
    if not isinstance(reference, LearningReference):
        raise LearningReferenceError("reference must be a LearningReference")
    if not isinstance(store, ExperienceStore):
        raise LearningReferenceError("store must implement the ExperienceStore protocol")

    if reference.is_empty:
        return GroundingVerification(
            GroundingVerdict.NO_REFERENCE, reference.candidate_principal, ()
        )

    results: list[CitationVerification] = []
    for citation in reference.citations:
        record = store.fetch(citation.reference_type, citation.reference_id)
        if record is None:
            results.append(CitationVerification(citation, CitationStatus.DANGLING))
            continue
        # The store's owner is authoritative; the candidate never authors it.
        if record.owning_principal == reference.candidate_principal:
            results.append(
                CitationVerification(citation, CitationStatus.SAME_PRINCIPAL, record)
            )
            continue
        if citation.owning_principal != record.owning_principal:
            results.append(
                CitationVerification(citation, CitationStatus.CLAIMED_OWNER_MISMATCH, record)
            )
            continue
        results.append(CitationVerification(citation, CitationStatus.GROUNDED, record))

    verdict = (
        GroundingVerdict.GROUNDED
        if all(item.grounded for item in results)
        else GroundingVerdict.UNGROUNDED
    )
    return GroundingVerification(verdict, reference.candidate_principal, tuple(results))
