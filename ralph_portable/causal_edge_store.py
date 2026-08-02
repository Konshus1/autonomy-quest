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
    formal_proof_evidence,
    is_guaranteed,
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

    # -- FORMAL LAYER: operator/CI-only (FORMAL_LAYER_SPEC §2-5, BB #746/#775) ---------------
    # These three methods actuate arm/verify/promote. They are invoked ONLY by the operator/CI
    # app.py routes, never by the autonomous loop (which stays mine + consult-predict + record-
    # surprise). The formal package (sandbox/registry) is imported lazily here so the sandbox
    # machinery only loads when an operator actuates — reinforcing "off the hot path".

    def _require(self, ident: tuple[str, str, str]) -> dict[str, Any]:
        edge = self._by_id.get(ident)
        if edge is None:
            raise KeyError(f"no edge with identity {ident!r}")
        return edge

    def attach_executor(self, ident: tuple[str, str, str], registry_key: str, registry: Any) -> dict[str, Any]:
        """ARM an edge with a digest-verified formal executor. Does NOT change formality/identity/
        support. Operator/CI-only."""
        from ralph_portable.formal.attach import attach_executor as _attach
        armed = _attach(self._require(ident), registry_key, registry)
        assert edge_identity(armed) == ident, "attach must not change edge identity"
        self._by_id[ident] = armed
        return armed

    def verify_and_record(self, ident: tuple[str, str, str], registry_key: str, registry: Any) -> dict[str, Any]:
        """Run the oracle for registry_key UNDER THE SANDBOX and record the outcome through the same
        earned-support channel. GREEN -> a formal_oracle_proof (confirm) + executor_verified=True;
        RED -> a non-confirming record; ERROR -> nothing recorded (fail-closed). Operator/CI-only."""
        from ralph_portable.formal.oracle_harness import run_oracle
        self._require(ident)
        result = run_oracle(registry_key, registry)
        evidence = formal_proof_evidence(result)
        if evidence is None:  # ERROR / not GREEN|RED -> record nothing
            return {"oracle": result, "recorded": False, "proposal": None}
        # A GREEN proof marks the executor verified BEFORE the proposal is computed, so the
        # evidential->formal gate (which requires executor_verified) sees the fresh proof in the
        # same call. (Setting it after record_evidence would compute a stale, still-unverified hold.)
        if result.get("result") == "GREEN":
            edge = self._require(ident)
            edge["executor_verified"] = True
            edge.setdefault("oracle_proof", []).append(
                {"registry_key": registry_key, "oracle_digest": result.get("oracle_digest")})
            assert edge_identity(edge) == ident, "verify must not change edge identity"
        proposal = self.record_evidence(ident, evidence)  # confirm bumps support via the one path
        return {"oracle": result, "recorded": True, "proposal": proposal}

    def apply_promotion(self, ident: tuple[str, str, str], proposal: dict[str, Any]) -> dict[str, Any]:
        """Actuate a GATED promotion proposal from propose_update. Sets formality to proposal['to'];
        on a formal+script edge sets predicted_certainty=1.0 (justified ONLY by the GREEN oracle that
        set executor_verified). Refuses formal without executor_verified (belt-and-suspenders vs the
        propose_update gate). Identity preserved. Operator/CI-only."""
        edge = self._require(ident)
        if proposal.get("action") != "promote":
            return edge
        to = proposal["to"]
        if to == "formal" and not edge.get("executor_verified"):
            raise ValueError("refusing to promote to formal without a verified formal executor (BB #775)")
        edge["formality"] = to
        if is_guaranteed(edge):  # formal AND directness=script
            edge["predicted_certainty"] = 1.0
        assert edge_identity(edge) == ident, "promotion must not change edge identity"
        self._by_id[ident] = edge
        return edge
