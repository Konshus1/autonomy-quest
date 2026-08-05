"""G1 / BB #2301 slice 1 — coherence frontier: pure, read-only, ground-truth-seeded ranking.

Builds realistic fixtures through InMemoryCausalEdgeStore + causal_edges.surprise (the same path
the live surprise loop writes), so the frontier is exercised against the real edge shape, and a
raw-list case for malformed-input defense.
"""

from __future__ import annotations

from ralph_portable.causal_edge_store import InMemoryCausalEdgeStore
from ralph_portable.causal_edges import (
    edge_identity,
    formal_proof_evidence,
    surprise,
)
from ralph_portable.coherence_frontier import (
    coherence_frontier,
    coherence_score,
    is_low_coherence,
    one_hop_neighbors,
)


def _edge(cause: str, effect: str, **over) -> dict:
    e = {
        "cause": cause,
        "effect": effect,
        "formality": "evidential",
        "strictness": "advisory",
        "directness": "predicate",
        "executor": {"kind": "predicate", "ref": f"checks/{effect}.sql"},
        "predicted_certainty": 0.8,
    }
    e.update(over)
    return e


def _store(*edges: dict) -> InMemoryCausalEdgeStore:
    s = InMemoryCausalEdgeStore()
    for e in edges:
        s.put(e)
    return s


# ---- basics: empty / confirm-only ----

def test_empty_store_yields_no_frontier():
    assert coherence_frontier([]) == []
    assert coherence_frontier(None) == []


def test_confirm_only_edges_are_not_low_coherence():
    s = _store(_edge("a", "e"))
    ident = edge_identity(_edge("a", "e"))
    s.record_evidence(ident, surprise(0.9, True))   # confirm -> not low-coherence
    assert coherence_frontier(s.all()) == []
    assert is_low_coherence(s.all()[0]) is False


# ---- low-coherence ranking ----

def test_demote_ranks_above_investigate_by_surprise():
    s = _store(_edge("a", "e1", predicted_certainty=0.9), _edge("b", "e2", predicted_certainty=0.4))
    s.record_evidence(edge_identity(_edge("a", "e1")), surprise(0.9, False))   # demote, mag 0.9
    s.record_evidence(edge_identity(_edge("b", "e2")), surprise(0.4, False))   # investigate, mag 0.4
    f = coherence_frontier(s.all())
    assert [i["cause"] for i in f] == ["a", "b"]
    assert f[0]["max_surprise"] == 0.9 and f[1]["max_surprise"] == 0.4
    assert f[0]["incoherence_records"] == 1


def test_refute_counts_as_max_surprise():
    s = _store(_edge("a", "e", formality="formal", directness="script",
                    executor={"kind": "script", "ref": "scripts/a.py"}, predicted_certainty=1.0))
    ident = edge_identity(_edge("a", "e", formality="formal", directness="script",
                                executor={"kind": "script", "ref": "scripts/a.py"}))
    s.record_evidence(ident, formal_proof_evidence(
        {"result": "RED", "registry_key": "k", "oracle_digest": "d", "reason": "no"}))
    f = coherence_frontier(s.all())
    assert f[0]["max_surprise"] == 1.0
    assert f[0]["incoherence_records"] == 1


# ---- seed + neighborhood ----

def test_seed_first_then_neighbors_then_other_low_coherence():
    s = _store(
        _edge("deploy", "measure_up", predicted_certainty=0.9),   # the surprised edge
        _edge("deploy", "cache_warm"),                             # neighbor (shares cause)
        _edge("refresh_cache", "measure_up"),                       # neighbor (shares effect)
        _edge("cache_warm", "user_happy"),                          # TWO-hop -> excluded
        _edge("send_email", "email_sent", predicted_certainty=0.85),# unrelated low-coherence
    )
    s.record_evidence(edge_identity(_edge("deploy", "measure_up")), surprise(0.9, False))
    s.record_evidence(edge_identity(_edge("send_email", "email_sent")), surprise(0.85, False))

    f = coherence_frontier(s.all(), surprised_edge=("deploy", "measure_up"))
    assert f[0]["seed"] is True and f[0]["cause"] == "deploy" and f[0]["effect"] == "measure_up"
    neighbors = {(i["cause"], i["effect"]) for i in f if i["neighbor"] is True}
    assert ("deploy", "cache_warm") in neighbors
    assert ("refresh_cache", "measure_up") in neighbors
    assert ("cache_warm", "user_happy") not in {(i["cause"], i["effect"]) for i in f}
    others = [i for i in f if i["seed"] is False and i["neighbor"] is False]
    assert any(i["cause"] == "send_email" for i in others)


def test_seed_not_in_store_is_still_named_and_neighborhood_derived():
    s = _store(_edge("ghost", "other_effect"), _edge("x", "measure_up"))
    f = coherence_frontier(s.all(), surprised_edge=("ghost", "measure_up"))
    assert f[0]["seed"] is True
    assert (f[0]["cause"], f[0]["effect"]) == ("ghost", "measure_up")
    # neighbors of ("ghost","measure_up") present: ("ghost","other_effect"), ("x","measure_up")
    nbr = {(i["cause"], i["effect"]) for i in f if i["neighbor"] is True}
    assert ("ghost", "other_effect") in nbr
    assert ("x", "measure_up") in nbr


def test_one_hop_neighbors_excludes_seed_by_pair():
    s = _store(_edge("a", "e"), _edge("a", "e2"), _edge("b", "e"))
    seed = _edge("a", "e")
    nbrs = one_hop_neighbors(s.all(), seed)
    keys = {(n["cause"], n["effect"]) for n in nbrs}
    assert ("a", "e") not in keys
    assert ("a", "e2") in keys and ("b", "e") in keys


# ---- bounds / dedup / determinism ----

def test_max_items_bounds_and_zero():
    s = _store(_edge("a", "e1", predicted_certainty=0.9), _edge("b", "e2", predicted_certainty=0.9))
    for c in ("a", "b"):
        s.record_evidence(edge_identity(_edge(c, f"e{1 if c=='a' else 2}")), surprise(0.9, False))
    assert len(coherence_frontier(s.all(), max_items=1)) == 1
    assert coherence_frontier(s.all(), max_items=0) == []
    assert coherence_frontier(s.all(), max_items=-3) == []
    assert coherence_frontier(s.all(), max_items=True) == []  # bool rejected


def test_neighbor_that_is_also_low_coherence_appears_once():
    s = _store(_edge("a", "e", predicted_certainty=0.9), _edge("a", "e2", predicted_certainty=0.9))
    s.record_evidence(edge_identity(_edge("a", "e")), surprise(0.9, False))
    s.record_evidence(edge_identity(_edge("a", "e2")), surprise(0.85, False))
    f = coherence_frontier(s.all(), surprised_edge=("a", "e"))
    assert sum(1 for i in f if (i["cause"], i["effect"]) == ("a", "e2")) == 1
    # the low-coherence neighbor is taken in the neighbor bucket, not duplicated in "others"
    assert ("a", "e2") in {(i["cause"], i["effect"]) for i in f if i["neighbor"] is True}


def test_deterministic_order_across_runs():
    s = _store(*[_edge(c, f"e{i}", predicted_certainty=0.7 + (i % 3) * 0.05)
                 for i, c in enumerate(("a", "b", "c", "d"))])
    for i, c in enumerate(("a", "b", "c", "d")):
        s.record_evidence(edge_identity(_edge(c, f"e{i}")), surprise(0.7 + (i % 3) * 0.05, False))
    a = coherence_frontier(s.all())
    b = coherence_frontier(list(reversed(s.all())))   # different input order
    assert a == b


def test_malformed_entries_skipped():
    junk = [None, "x", {"cause": ""}, {"effect": "e"}, {"cause": "a", "effect": "e", "predicted_certainty": 0.9}]
    f = coherence_frontier(junk)   # only the last is valid; it has no evidence -> []
    assert f == []
    # with low-coherence on the valid one, it appears
    s = _store(_edge("a", "e", predicted_certainty=0.9))
    s.record_evidence(edge_identity(_edge("a", "e")), surprise(0.9, False))
    f2 = coherence_frontier([None, "x", {"cause": ""}] + s.all())
    assert len(f2) == 1 and f2[0]["cause"] == "a"


def test_frontier_does_not_mutate_input_edges():
    s = _store(_edge("a", "e", predicted_certainty=0.9))
    s.record_evidence(edge_identity(_edge("a", "e")), surprise(0.9, False))
    before_keys = {id(e): set(e.keys()) for e in s.all()}
    coherence_frontier(s.all())
    for e in s.all():
        assert set(e.keys()) == before_keys[id(e)]


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))