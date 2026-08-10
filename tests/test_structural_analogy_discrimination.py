"""Load-bearing discrimination for graph-constrained analogy (task #4834).

The target and same-domain distractor deliberately share surface words.  The
cross-domain firebreak shares relational structure instead.  Passing this test
is the minimum evidence for the mechanism; no larger benchmark matters until it
passes.
"""

from ralph_portable.structural_analogy import (
    RetrievalSignature,
    Situation,
    Relation,
    retrieve_analogy,
    compare_retrieval_regimes,
)


def _fixture():
    target = Situation(
        id="malware_lateral_target",
        domain="cybersecurity",
        title="Contain malware on a flat network",
        summary="Malware spreads across network hosts; preserve business service while containing it.",
        signature=RetrievalSignature(
            goal=("contain propagation", "protect unaffected assets"),
            constraints=("preserve service", "bounded disruption"),
            failed_predictions=("host-by-host inspection is fast enough",),
            domain="cybersecurity",
        ),
        roles={
            "process": "malware process",
            "network": "flat peer network",
            "systems": "uncompromised business systems",
        },
        observed_relations=(
            Relation("process", "moves_laterally_through", "network", "propagation"),
            Relation("network", "puts_at_risk", "systems", "exposure"),
        ),
    )
    cross_domain = Situation(
        id="wildfire_firebreak",
        domain="wildfire_management",
        title="Firebreak containment",
        summary="A firebreak interrupts connected vegetation to contain a wildfire.",
        signature=RetrievalSignature(
            goal=("contain propagation", "protect unaffected assets"),
            constraints=("preserve access", "bounded disruption"),
            failed_predictions=("local suppression is fast enough",),
            domain="wildfire_management",
        ),
        roles={
            "fire": "advancing wildfire",
            "fuel": "connected dry vegetation",
            "community": "unburned communities",
            "barrier": "firebreak",
        },
        observed_relations=(
            Relation("fire", "spreads_through", "fuel", "propagation"),
            Relation("fuel", "exposes", "community", "exposure"),
        ),
        transferable_relations=(
            Relation("barrier", "interrupts", "fuel", "interruption"),
            Relation("barrier", "protects", "community", "protection"),
        ),
    )
    surface_distractor = Situation(
        id="cybersecurity_malware_scanner",
        domain="cybersecurity",
        title="Malware network host containment scanner",
        summary="Cybersecurity malware network hosts are scanned while business service is preserved.",
        # Same cheap signature as the target. Stage 1 therefore prefers this
        # same-domain case; only Stage 2 may reject it.
        signature=RetrievalSignature(
            goal=("contain propagation", "protect unaffected assets"),
            constraints=("preserve service", "bounded disruption"),
            failed_predictions=("host-by-host inspection is fast enough",),
            domain="cybersecurity",
        ),
        roles={"scanner": "malware scanner", "host": "network host", "report": "scan report"},
        observed_relations=(
            Relation("scanner", "observes", "host", "observation"),
            Relation("scanner", "produces", "report", "recording"),
        ),
        transferable_relations=(
            Relation("report", "prioritizes", "host", "triage"),
        ),
    )
    unrelated = Situation(
        id="double_entry",
        domain="accounting",
        title="Double-entry reconciliation",
        summary="Two ledger entries expose an accounting mismatch.",
        signature=RetrievalSignature(
            goal=("localize mismatch",), constraints=("preserve audit trail",),
            failed_predictions=("one record is sufficient",), domain="accounting"
        ),
        roles={"event": "transaction", "left": "debit", "right": "credit"},
        observed_relations=(
            Relation("event", "produces", "left", "dual_record"),
            Relation("event", "produces", "right", "dual_record"),
        ),
    )
    shallow_distractor = Situation(
        id="cybersecurity_alert_dashboard",
        domain="cybersecurity",
        title="Network malware alert dashboard",
        summary="A dashboard groups malware alerts while preserving service.",
        signature=RetrievalSignature(
            goal=("group alerts",), constraints=("preserve service",),
            failed_predictions=("alerts are self-prioritizing",), domain="cybersecurity"
        ),
        roles={"alert": "alert", "view": "dashboard"},
        observed_relations=(Relation("view", "groups", "alert", "aggregation"),),
    )
    random_case = Situation(
        id="warehouse_reorder",
        domain="supply_chain",
        title="Warehouse reorder point",
        summary="Inventory levels trigger replenishment.",
        signature=RetrievalSignature(
            goal=("avoid stockout",), constraints=("bounded inventory",),
            failed_predictions=("demand is constant",), domain="supply_chain"
        ),
        roles={"stock": "inventory", "signal": "reorder signal"},
        observed_relations=(Relation("stock", "triggers", "signal", "feedback"),),
    )
    return target, [surface_distractor, cross_domain, shallow_distractor, unrelated, random_case]


def test_structural_match_retrieves_cross_domain_and_rejects_surface_distractor():
    target, library = _fixture()

    result = retrieve_analogy(target, library, shortlist_size=2)

    # Stage 1 is cheap and preserves both hypotheses. Domain helps cost, but is
    # not allowed to define relevance or remove Einstein's-elevator candidates.
    assert len(library) > len(result.shortlist) == 2
    assert [row.situation.id for row in result.shortlist] == [
        "cybersecurity_malware_scanner", "wildfire_firebreak"
    ]
    assert result.shortlist[0].cheap_score > result.shortlist[1].cheap_score

    controls = compare_retrieval_regimes(target, library, shortlist_size=2)
    assert controls.surface_source_id == "cybersecurity_malware_scanner"
    assert controls.structural_source_id == "wildfire_firebreak"

    # Stage 2 makes the actual discrimination on relation families + topology.
    assert result.selected.source.id == "wildfire_firebreak"
    assert result.selected.source.domain != target.domain
    assert result.selected.role_mapping == {
        "community": "systems", "fire": "process", "fuel": "network"
    }
    rejected = {row.source.id: row for row in result.structural_candidates if not row.accepted}
    assert "cybersecurity_malware_scanner" in rejected
    assert rejected["cybersecurity_malware_scanner"].matched_relation_count == 0

    # Stage 3 uses the map: it projects source transfer relations into plan
    # requirements over the ORIGINAL target, rather than returning similarity.
    proposal = result.plan_proposal
    assert proposal.target_id == target.id
    assert proposal.source_id == "wildfire_firebreak"
    assert {(r.target_role, r.source_role) for r in proposal.proposed_roles} == {
        ("proposed_barrier", "barrier")
    }
    assert proposal.authority == "advisory_hypothesis"
    requirements = {(r.subject, r.family, r.object) for r in proposal.required_relations}
    assert requirements == {
        ("proposed_barrier", "interruption", "network"),
        ("proposed_barrier", "protection", "systems"),
    }
