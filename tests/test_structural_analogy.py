from dataclasses import replace

import pytest

from ralph_portable.structural_analogy import (
    Relation,
    RetrievalSignature,
    Situation,
    cheap_shortlist,
    retrieve_analogy,
    structural_match,
)
from tests.test_structural_analogy_discrimination import _fixture


def _sig(domain="source"):
    return RetrievalSignature(("reach goal",), ("bounded cost",), ("old path failed",), domain)


def test_systematicity_prefers_connected_relations_over_equal_sized_bag():
    target = Situation(
        "target", "target", "", "", _sig("target"),
        {key: key for key in ("a", "b", "c", "x", "y")},
        (
            Relation("a", "r1", "b", "f1"),
            Relation("b", "r2", "c", "f2"),
            Relation("x", "r3", "x", "f3"),
            Relation("y", "r4", "y", "f4"),
        ),
    )
    connected = Situation(
        "connected", "other", "", "", _sig(), {"p": "p", "q": "q", "r": "r"},
        (Relation("p", "s1", "q", "f1"), Relation("q", "s2", "r", "f2")),
    )
    independent = Situation(
        "independent", "other", "", "", _sig(), {"i": "i", "j": "j"},
        (Relation("i", "s3", "i", "f3"), Relation("j", "s4", "j", "f4")),
    )

    deep = structural_match(connected, target)
    bag = structural_match(independent, target)

    assert deep.matched_relation_count == bag.matched_relation_count == 2
    assert deep.largest_connected_component == 2
    assert bag.largest_connected_component == 1
    assert deep.systematicity_score > bag.systematicity_score
    assert deep.accepted is True
    assert bag.accepted is False


def test_same_text_and_families_lose_when_shared_role_topology_is_broken():
    target, library = _fixture()
    firebreak = next(row for row in library if row.id == "wildfire_firebreak")
    broken = replace(
        firebreak,
        id="wildfire_broken_topology",
        # Same family multiset and all surface/signature fields; only endpoints change.
        observed_relations=(
            Relation("fire", "spreads_through", "fuel", "propagation"),
            Relation("community", "exposes", "fuel", "exposure"),
        ),
    )

    good = structural_match(firebreak, target)
    bad = structural_match(broken, target)

    assert good.accepted is True
    assert bad.accepted is False
    assert bad.matched_relation_count == 1


def test_renaming_surface_and_roles_preserves_structural_verdict():
    target, library = _fixture()
    firebreak = next(row for row in library if row.id == "wildfire_firebreak")
    renamed = Situation(
        id="opaque", domain="unrelated-domain", title="ZXQ", summary="No shared words", signature=_sig(),
        roles={"u1": "opaque one", "u2": "opaque two", "u3": "opaque three", "u4": "opaque four"},
        observed_relations=(
            Relation("u1", "p9", "u2", "propagation"),
            Relation("u2", "p8", "u3", "exposure"),
        ),
        transferable_relations=(Relation("u4", "p7", "u2", "interruption"),),
    )
    original = structural_match(firebreak, target)
    metamorphic = structural_match(renamed, target)
    assert metamorphic.accepted is True
    assert metamorphic.systematicity_score == original.systematicity_score
    assert metamorphic.matched_relation_count == original.matched_relation_count


def test_attributes_never_add_to_structural_score():
    target, library = _fixture()
    distractor = next(row for row in library if row.id == "cybersecurity_malware_scanner")
    padded = replace(
        distractor,
        observed_relations=distractor.observed_relations + tuple(
            Relation("scanner", f"looks_like_{i}", "host", "propagation", kind="attribute")
            for i in range(20)
        ),
    )
    assert structural_match(padded, target).matched_relation_count == 0


def test_no_structural_analogue_abstains_instead_of_forcing_transfer():
    target, library = _fixture()
    distractors = [row for row in library if row.id != "wildfire_firebreak"]
    with pytest.raises(LookupError, match="no accepted structural analogue"):
        retrieve_analogy(target, distractors, shortlist_size=len(distractors))


def test_stage_one_never_reads_graph_or_surface_fields():
    class CheapOnly:
        id = "cheap-only"
        domain = "other"
        signature = _sig("other")

        @property
        def roles(self):
            raise AssertionError("stage 1 read roles")

        @property
        def observed_relations(self):
            raise AssertionError("stage 1 read graph")

        @property
        def title(self):
            raise AssertionError("stage 1 read surface title")

    rows = cheap_shortlist(_sig("target"), [CheapOnly()], limit=1)
    assert rows[0].situation.id == "cheap-only"
