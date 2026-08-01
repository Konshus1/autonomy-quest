"""Causal-edge store — slice 1b (BB #746). Stdlib-only, identity-keyed.

Keys edges by their IMMUTABLE identity (``edge_identity`` = cause/effect/scope). The dials
(formality/strictness/directness), predicted_certainty, support_count, and accumulated
surprise EVIDENCE are mutable STATE attached to that identity — never part of the key
(ccf-1557 C2 invariant; keeps evidence-binding idempotent-rerun-safe).

This in-memory store is the portable contract + the demonstrable plan->certainty->surprise
flow. The AGE/Postgres-backed store (persisting the same shape) is a later slice.
"""

from __future__ import annotations

from typing import Any

from ralph_portable.causal_edges import (
    edge_identity,
    plan_certainty,
    propose_update,
    validate_causal_edge,
)


class InMemoryCausalEdgeStore:
    def __init__(self) -> None:
        self._by_id: dict[tuple[str, str, str], dict[str, Any]] = {}

    def put(self, edge: dict[str, Any]) -> tuple[str, str, str]:
        """Validate + UPSERT by identity. Updating an edge (e.g. a promotion changed its
        dials) preserves accumulated support/evidence unless the caller overrides them."""
        errors = validate_causal_edge(edge)
        if errors:
            raise ValueError("; ".join(errors))
        ident = edge_identity(edge)
        existing = self._by_id.get(ident)
        new = dict(edge)
        if existing is not None:
            new.setdefault("support_count", existing.get("support_count", 0))
            new.setdefault("evidence", existing.get("evidence", []))
        self._by_id[ident] = new
        return ident

    def get(self, ident: tuple[str, str, str]) -> dict[str, Any] | None:
        return self._by_id.get(ident)

    def all(self) -> list[dict[str, Any]]:
        return list(self._by_id.values())

    def governing(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Edges that govern any plan step, matched on (cause, effect) == (action, effect)."""
        keys = {(str(s.get("action")), str(s.get("effect"))) for s in steps}
        return [e for e in self._by_id.values() if (str(e["cause"]), str(e["effect"])) in keys]

    def assess_plan(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        """The planning check: score a plan against the stored causal model (slice 1a math)."""
        return plan_certainty(steps, self.all())

    def record_evidence(self, ident: tuple[str, str, str], surprise_result: dict[str, Any]) -> dict[str, Any]:
        """Attach a surprise as EVIDENCE on the edge and return a GATED update proposal.

        A ``confirm`` bumps support_count (earns toward promotion); nothing is auto-applied —
        the returned proposal is for the learning loop / operator to actuate.
        """
        edge = self._by_id.get(ident)
        if edge is None:
            raise KeyError(f"no edge with identity {ident!r}")
        edge.setdefault("evidence", []).append(surprise_result)
        if surprise_result.get("signal") == "confirm":
            edge["support_count"] = int(edge.get("support_count") or 0) + 1
        return propose_update(edge, surprise_result)
