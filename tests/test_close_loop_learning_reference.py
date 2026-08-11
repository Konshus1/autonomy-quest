"""Red-first tests for learning-reference propagation and grounding verification.

Each of the design's named scenarios is asserted on *content*, not existence:
a dangling citation must be rejected with the DANGLING status, a same-principal
citation with SAME_PRINCIPAL, and a genuine one must return the retrieved record
for the evaluator.  These fail against a stub that "always grounds" and pass only
against the real separate-principal checks.
"""
import base64

import pytest

from runner.close_loop.hashing import task_intent_hash
from runner.close_loop.learning_reference import (
    CitationStatus,
    ExperienceCitation,
    ExperienceRecord,
    GroundingVerdict,
    InMemoryExperienceStore,
    LearningReference,
    LearningReferenceError,
    ReferenceType,
    TRAILER_LINEAGE,
    parse_commit_trailers,
    verify_learning_reference,
    verify_lineage_integrity,
)
from runner.consultants.self_correction import ReferenceEvent, ReferenceKind

CANDIDATE = "aq_worker:candidate-7"
EVALUATOR = "aq_evaluator"
CORRECTOR = "human:kevin"


def _intent_hash() -> str:
    task = {
        "id": 99,
        "title": "Ground a learning",
        "description": "one slice",
        "parent_task_id": None,
        "details": {
            "aq_phase1": {
                "intent": {
                    "repo_id": "aq",
                    "target_ref": "refs/heads/main",
                    "scope": ["runner/close_loop"],
                    "definition_of_done": ["tests pass"],
                    "stop_condition": "one slice",
                    "verifier_manifest": {"id": "v1", "sha256": "a" * 64},
                    "authority": {"class": "local_dev"},
                }
            }
        },
    }
    return task_intent_hash(task)


def _genuine_store() -> InMemoryExperienceStore:
    return InMemoryExperienceStore(
        [
            ExperienceRecord(ReferenceType.CORRECTION, "corr-1", CORRECTOR,
                             {"corrected_classification": "keep"}),
            ExperienceRecord(ReferenceType.GROUNDED_PRINCIPLE, "prin-9", EVALUATOR,
                             {"cause": "x", "effect": "y"}),
        ]
    )


def _reference(*citations: ExperienceCitation) -> LearningReference:
    return LearningReference(CANDIDATE, citations)


# --- 1. dangling citation is rejected ------------------------------------- #

def test_dangling_citation_is_rejected_as_ungrounded():
    reference = _reference(
        ExperienceCitation(ReferenceType.CORRECTION, "does-not-exist", CORRECTOR)
    )
    result = verify_learning_reference(reference, _genuine_store())
    assert result.verdict is GroundingVerdict.UNGROUNDED
    assert not result.is_grounded
    assert result.citations[0].status is CitationStatus.DANGLING
    assert result.grounded_experiences == ()
    assert result.reason_codes() == ("dangling:correction:does-not-exist",)


# --- 2. same-principal citation is rejected ------------------------------- #

def test_citation_owned_by_candidate_is_not_independently_grounded():
    store = InMemoryExperienceStore(
        # The record exists, but the candidate itself owns it.
        [ExperienceRecord(ReferenceType.CORRECTION, "self-1", CANDIDATE)]
    )
    reference = _reference(ExperienceCitation(ReferenceType.CORRECTION, "self-1", CANDIDATE))
    result = verify_learning_reference(reference, store)
    assert result.verdict is GroundingVerdict.UNGROUNDED
    assert result.citations[0].status is CitationStatus.SAME_PRINCIPAL
    assert result.grounded_experiences == ()


# --- 3. genuine separate-principal citations ground and are returned ------ #

def test_genuine_separate_principal_citations_are_grounded_and_returned():
    reference = _reference(
        ExperienceCitation(ReferenceType.CORRECTION, "corr-1", CORRECTOR),
        ExperienceCitation(ReferenceType.GROUNDED_PRINCIPLE, "prin-9", EVALUATOR),
    )
    result = verify_learning_reference(reference, _genuine_store())
    assert result.verdict is GroundingVerdict.GROUNDED
    assert result.is_grounded
    assert all(item.status is CitationStatus.GROUNDED for item in result.citations)
    returned = {(r.reference_type, r.reference_id) for r in result.grounded_experiences}
    assert returned == {
        (ReferenceType.CORRECTION, "corr-1"),
        (ReferenceType.GROUNDED_PRINCIPLE, "prin-9"),
    }
    # The retrieved payloads are handed back for the evaluator to judge.
    assert result.grounded_experiences[0].payload  # non-empty content carried through


def test_one_bad_citation_taints_the_whole_reference():
    reference = _reference(
        ExperienceCitation(ReferenceType.CORRECTION, "corr-1", CORRECTOR),
        ExperienceCitation(ReferenceType.CORRECTION, "ghost", CORRECTOR),
    )
    result = verify_learning_reference(reference, _genuine_store())
    assert result.verdict is GroundingVerdict.UNGROUNDED
    # The good citation still resolved; the reference as a whole is refused.
    assert len(result.grounded_experiences) == 1
    assert any(item.status is CitationStatus.DANGLING for item in result.citations)


# --- 4. no learning reference -> not an error ----------------------------- #

def test_empty_reference_is_no_grounding_to_check_not_an_error():
    reference = _reference()  # ordinary non-learning change
    assert reference.is_empty
    result = verify_learning_reference(reference, _genuine_store())
    assert result.verdict is GroundingVerdict.NO_REFERENCE
    assert not result.has_reference
    assert not result.is_grounded  # "no reference" is not "grounded"
    assert result.grounded_experiences == ()
    assert result.reason_codes() == ()


# --- 5. tamper detection via the lineage/integrity hash ------------------- #

def test_altering_a_citation_after_emission_breaks_the_lineage_hash():
    intent_hash = _intent_hash()
    reference = _reference(ExperienceCitation(ReferenceType.CORRECTION, "corr-1", CORRECTOR))
    emitted_lineage = reference.lineage_hash(intent_hash)
    assert verify_lineage_integrity(reference, intent_hash, emitted_lineage)

    # An adversary swaps the cited experience for one they control.
    tampered = _reference(ExperienceCitation(ReferenceType.CORRECTION, "corr-evil", CORRECTOR))
    assert tampered.reference_digest != reference.reference_digest
    assert not verify_lineage_integrity(tampered, intent_hash, emitted_lineage)


def test_lineage_hash_also_binds_the_intent_hash():
    reference = _reference(ExperienceCitation(ReferenceType.CORRECTION, "corr-1", CORRECTOR))
    a = reference.lineage_hash("a" * 64)
    b = reference.lineage_hash("b" * 64)
    assert a != b  # rebasing onto a different admitted intent breaks the binding


# --- commit-trailer propagation round-trips and stays tamper-evident ------ #

def test_commit_trailer_round_trips_the_reference():
    intent_hash = _intent_hash()
    reference = _reference(
        ExperienceCitation(ReferenceType.REFERENCE_EVENT, "evt-3", EVALUATOR),
        ExperienceCitation(ReferenceType.CORRECTION, "corr-1", CORRECTOR),
    )
    trailers = reference.commit_trailer_lines(intent_hash)
    message = "fix(x): learn the type\n\nbody\n\n" + "\n".join(trailers)

    parsed = parse_commit_trailers(message)
    assert parsed is not None
    assert parsed.reference == reference  # order-independent equality via canonical sort
    assert parsed.declared_digest == reference.reference_digest
    assert verify_lineage_integrity(reference, intent_hash, parsed.declared_lineage)


def test_ordinary_commit_without_trailer_parses_as_no_reference():
    assert parse_commit_trailers("chore: bump dep\n\nno learning here") is None


def test_tampered_trailer_payload_breaks_the_declared_lineage():
    intent_hash = _intent_hash()
    reference = _reference(ExperienceCitation(ReferenceType.CORRECTION, "corr-1", CORRECTOR))
    trailers = list(reference.commit_trailer_lines(intent_hash))

    # Forge the payload line to cite an experience the candidate controls,
    # keeping the original (now stale) lineage trailer.
    forged = _reference(ExperienceCitation(ReferenceType.CORRECTION, "corr-evil", CANDIDATE))
    forged_payload = base64.b64encode(
        __import__("json").dumps(forged.payload(), sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    trailers[0] = f"Learning-Reference: {forged_payload}"
    message = "subject\n\n" + "\n".join(trailers)

    parsed = parse_commit_trailers(message)
    assert parsed is not None
    assert parsed.reference == forged
    # The stale lineage trailer no longer matches the forged payload -> caught.
    assert not verify_lineage_integrity(parsed.reference, intent_hash, parsed.declared_lineage)


def test_malformed_trailer_payload_fails_closed():
    with pytest.raises(LearningReferenceError):
        parse_commit_trailers(f"subject\n\n{__trailer('!!not-base64!!')}")


def __trailer(value: str) -> str:
    return f"Learning-Reference: {value}"


# --- provenance-lie and reuse-of-ReferenceEvent shape --------------------- #

def test_claimed_owner_that_disagrees_with_the_store_is_rejected():
    store = InMemoryExperienceStore(
        [ExperienceRecord(ReferenceType.CORRECTION, "corr-1", CORRECTOR)]
    )
    # The candidate claims a different (independent-looking) owner than the truth.
    reference = _reference(
        ExperienceCitation(ReferenceType.CORRECTION, "corr-1", "human:someone-else")
    )
    result = verify_learning_reference(reference, store)
    assert result.verdict is GroundingVerdict.UNGROUNDED
    assert result.citations[0].status is CitationStatus.CLAIMED_OWNER_MISMATCH


def test_citation_reuses_reference_event_shape():
    event = ReferenceEvent(
        reference_kind=ReferenceKind.COLLECTION_MEMBER,
        generating_rule_id="rule.alpha",
        old_classification="drop",
        corrected_classification="keep",
        has_shared_generating_rule=True,
    )
    citation = ExperienceCitation.for_reference_event("evt-3", EVALUATOR, event)
    assert citation.reference_type is ReferenceType.REFERENCE_EVENT
    assert citation.reference_id == "evt-3"
    assert citation.owning_principal == EVALUATOR


# --- construction is strict / fail-closed --------------------------------- #

def test_duplicate_citations_are_rejected_at_construction():
    with pytest.raises(LearningReferenceError):
        _reference(
            ExperienceCitation(ReferenceType.CORRECTION, "corr-1", CORRECTOR),
            ExperienceCitation(ReferenceType.CORRECTION, "corr-1", CORRECTOR),
        )


def test_unknown_reference_type_is_rejected():
    with pytest.raises(ValueError):
        ExperienceCitation("not_a_type", "x", CORRECTOR)  # type: ignore[arg-type]


def test_blank_candidate_principal_is_rejected():
    with pytest.raises(LearningReferenceError):
        LearningReference("   ", ())
