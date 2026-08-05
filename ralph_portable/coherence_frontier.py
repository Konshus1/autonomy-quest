"""Coherence frontier — the graph-derived, ground-truth-seeded curiosity frontier (BB #2301 / G1).

Curiosity today is FRONTIER-driven: it explores an operator-seeded external list (BB #587: the
frontier must be ground-truth-seeded, never loop-chosen — the 50,082-model runaway WAS degenerate
curiosity from a loop-chosen frontier). This module derives a SECOND kind of frontier from the
causal edge store: the edges where the WORLD has disagreed with the model — high-surprise /
low-coherence edges — plus, when a specific incoherence event TRIGGERED the probe, the one-hop
graph neighborhood of the just-surprised edge.

Ground-truth seeding (BB #587), restated for this frontier: it is built from RECORDED surprise
EVIDENCE on the edges — facts written by the surprise loop when outcomes contradicted predictions
(causal_edges.surprise -> store.record_evidence) — not from curiosity's own assertion of what might
be interesting. Curiosity does not get to declare its own frontier; it reads where the model was
already wrong. A confident-but-failed prediction (signal=``demote``) is the strongest signal; an
inconclusive one (``investigate``) is weaker; a refuted formal oracle proof (``refute``) is the
maximum — the 'guaranteed' claim was contradicted by deductive evidence.

PURE + READ-ONLY: this module never writes support, evidence, or any edge field, and it stages
nothing itself. It only DERIVES the frontier (a list of small descriptor dicts); actuation is the
loop's existing curiosity path, which STAGES inert proposals (never changes behavior) inside its
bounded budget/ratchet. There is no DB, no network, no I/O here — feed it a list of edge dicts
(the store's ``all()`` shape, or the management API's ``/api/causal/edges`` items) and it returns
a deterministic, identity-stable ranking.

DEFERRED GAP (per co-mentor sign-off, BB #587): the guardian overshoot/satisfaction tripwire that
bounds the frontier-driven path does NOT yet apply to the coherence frontier's exploration metric
(the surprise backlog). Slice 1/2 floor is budget+ratchet+cost ONLY; the backlog overshoot
tripwire is a later slice. The probe cannot run away in the meanwhile: it is bounded by
curiosity.budget.cycles_per_day AND by the rate of real incoherence events (themselves bounded by
mission cycle rate) — so a runaway here would require a runaway in the mission itself, which the
mission's OWN guardian already catches.
"""

from __future__ import annotations

from typing import Any

from .causal_edges import edge_identity

# Surprise-loop signals that mark an edge as low-coherence (the world disagreed with the model).
# "confirm" is the opposite — supporting evidence, NOT low-coherence. "refute" (a formal oracle
# returned RED on a 'guaranteed' claim) has no numeric surprise magnitude and is scored at the
# maximum (1.0): the strongest incoherence an edge can carry.
LOW_COHERENCE_SIGNALS = ("demote", "investigate", "refute")


def _edge_key(edge: dict[str, Any]) -> tuple[str, str]:
    """The (cause, effect) pair used for graph-neighborhood matching — NOT the full identity."""
    return (str(edge.get("cause")), str(edge.get("effect")))


def _as_seed(surprised_edge: Any) -> tuple[str, str] | None:
    """Normalize a seed spec (dict or (cause, effect) pair/tuple/list) to a (cause, effect) pair.

    Returns None for anything that does not name a real (cause, effect). A seed with a blank node
    is rejected: curiosity never probes a nameless spot.
    """
    if isinstance(surprised_edge, dict):
        c, f = surprised_edge.get("cause"), surprised_edge.get("effect")
    elif isinstance(surprised_edge, (tuple, list)) and len(surprised_edge) >= 2:
        c, f = surprised_edge[0], surprised_edge[1]
    else:
        return None
    c, f = str(c or "").strip(), str(f or "").strip()
    if not c or not f:
        return None
    return (c, f)


def low_coherence_records(edge: dict[str, Any]) -> list[dict[str, Any]]:
    """The recorded surprise EVIDENCE on an edge that marks it low-coherence (NOT confirms)."""
    out: list[dict[str, Any]] = []
    for ev in (edge.get("evidence") or []):
        if isinstance(ev, dict) and ev.get("signal") in LOW_COHERENCE_SIGNALS:
            out.append(ev)
    return out


def is_low_coherence(edge: dict[str, Any]) -> bool:
    """An edge carrying at least one demote/investigate/refute evidence record."""
    return bool(low_coherence_records(edge))


def coherence_score(edge: dict[str, Any]) -> tuple[float, int]:
    """(max_surprise_magnitude, low_coherence_count) for an edge's incoherence evidence.

    A refuted formal proof (signal=``refute``) has no numeric surprise magnitude (the oracle
    disagreed with a 'guaranteed' claim, not a probabilistic prediction) and is scored at the
    maximum 1.0 — the strongest incoherence an edge can carry. ``demote``/``investigate``
    records carry their own ``surprise`` magnitude (|predicted - actual|). Records with a
    malformed/missing magnitude contribute 0.0 to the max but still count.
    """
    best = 0.0
    n = 0
    for ev in low_coherence_records(edge):
        n += 1
        if ev.get("signal") == "refute":
            best = max(best, 1.0)
            continue
        try:
            best = max(best, float(ev.get("surprise") or 0.0))
        except (TypeError, ValueError):
            continue
    return (round(best, 4), n)


def one_hop_neighbors(edges: list[dict[str, Any]], seed: dict[str, Any]) -> list[dict[str, Any]]:
    """Edges one hop from the seed in the (cause, effect) graph.

    The seed edge is (cause=X, effect=Y); a neighbor is any edge sharing a NODE with it (X or Y in
    EITHER position) — the context an incoherence probe should inspect even if those edges have
    not themselves been surprised, because they may be implicated. The seed is excluded BY
    (cause, effect) pair: a same-named edge (including a scope variant) is not its own neighbor.
    """
    sc, se = _edge_key(seed)
    nodes = {sc, se}
    out: list[dict[str, Any]] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        c, f = _edge_key(e)
        if (c, f) == (sc, se):
            continue
        if c in nodes or f in nodes:
            out.append(e)
    return out


def _item(edge: dict[str, Any], *, seed: bool, neighbor: bool) -> dict[str, Any]:
    """A small frontier-item descriptor — derived, not the edge itself, so callers can't mutate
    the store by editing a frontier row."""
    ms, n = coherence_score(edge)
    return {
        "cause": str(edge.get("cause")),
        "effect": str(edge.get("effect")),
        "scope": edge.get("scope") or {},
        "formality": edge.get("formality"),
        "predicted_certainty": edge.get("predicted_certainty"),
        "max_surprise": ms,
        "incoherence_records": n,
        "seed": seed,
        "neighbor": neighbor,
    }


def _sort_key(edge: dict[str, Any]) -> tuple[Any, ...]:
    """Rank low-coherence edges by strongest single contradiction first, then by breadth of
    contradicting evidence, then deterministically by identity. Deterministic across runs and
    stores — never depends on dict insertion order or a float tie."""
    ms, n = coherence_score(edge)
    ident = edge_identity(edge)
    return (-ms, -n, ident[0], ident[1], ident[2])


def coherence_frontier(
    edges: list[dict[str, Any]] | None,
    *,
    surprised_edge: Any = None,
    max_items: int = 5,
) -> list[dict[str, Any]]:
    """Derive the high-surprise / low-coherence frontier for a curiosity probe.

    edges:          iterable of causal-edge dicts (the store's ``all()`` shape, or the management
                    API's ``/api/causal/edges`` items). Non-dict / missing-cause-effect entries are
                    skipped defensively — a frontier is never built from malformed claims.

    surprised_edge: optional (cause, effect) tuple or edge dict — the edge whose incoherence
                    TRIGGERED the probe. When given, the frontier is biased to its neighborhood:
                      [seed] -> one-hop neighbors (by score desc) -> other low-coherence edges
                    When omitted, the frontier is just the low-coherence edges by score desc
                    (a periodic sweep — future use; the incoherence-triggered path always names
                    a seed in Slice 3).

    max_items:      hard bound on the frontier size. Non-int, bool, or <= 0 -> empty frontier
                    (the probe then stages nothing; this is the right behavior when the budget is
                    zero or the caller wants the frontier suppressed).

    Returns a list of frontier-item dicts (see ``_item``). Read-only: the input edges are not
    mutated and no edge-state is written.
    """
    if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items <= 0:
        return []

    clean = [
        e for e in (edges or [])
        if isinstance(e, dict)
        and str(e.get("cause") or "").strip()
        and str(e.get("effect") or "").strip()
    ]

    seed_pair = _as_seed(surprised_edge)
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def _take(edge: dict[str, Any], *, seed: bool = False, neighbor: bool = False) -> bool:
        ident = edge_identity(edge)
        if ident in seen:
            return False
        seen.add(ident)
        items.append(_item(edge, seed=seed, neighbor=neighbor))
        return len(items) >= max_items

    # (1) Seed first — named by the triggering incoherence event, even if the fetched edge list
    #     has not yet absorbed the fresh surprise record (store drift / fetch-before-record). The
    #     event naming it is the ground-truth signal, not the score on the list.
    if seed_pair is not None:
        seed_edge = next((e for e in clean if _edge_key(e) == seed_pair), None)
        if seed_edge is None:
            seed_edge = {"cause": seed_pair[0], "effect": seed_pair[1], "scope": {}}
        if _take(seed_edge, seed=True):
            return items
        # (2) One-hop neighbors — context, included even without their own surprise evidence.
        for e in sorted(one_hop_neighbors(clean, seed_edge), key=_sort_key):
            if _take(e, neighbor=True):
                return items

    # (3) The rest of the low-coherence edges, ranked. Non-seed, non-neighbor contradictions are
    #     the secondary frontier — other places the model was wrong, surfaced after the seed's
    #     own neighborhood so an incoherence-triggered probe stays focused.
    for e in sorted((e for e in clean if is_low_coherence(e)), key=_sort_key):
        if _take(e, seed=False, neighbor=False):
            return items

    return items