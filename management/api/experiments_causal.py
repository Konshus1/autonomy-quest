"""Adapter: the live AQ loop <-> the EXPERIMENTAL causal-edge track (T5/T6, #4897/#4898).

WHY THIS FILE EXISTS
--------------------
experiments/causal_edges/{planning_check,surprise_wiring}.py were built to be
import-safe and main-path-free — their own docstrings guarantee they are not
imported by runner/loop.py, and their test suite asserts it. That guarantee is
worth keeping, but it also meant they had ZERO production callers: T6 was built
and marked done while never running (task #4898's "done" flip of 2026-08-08
06:28 cited code that nothing invoked).

This adapter is the seam. Every import of the experimental tree happens lazily,
INSIDE a function, so:
  - the main path never imports experiments/ at module scope,
  - a missing psycopg2 / absent schema degrades to a clean skip rather than an
    ImportError at startup,
  - the loop reaches T5/T6 over HTTP (management API), exactly as T10/T11 are
    reached via runner/causal_sync — one established pattern, not two.

THE PRODUCER/CONSUMER SEAM
--------------------------
    T5 PlanningCheck.check_and_record(plan)  -> INSERT planning_prediction   (pre-act)
    T6 SurpriseWiring.resolve_outcome(...)   -> resolves rows where actual_outcome IS NULL
Wiring T6 alone is a no-op: with no producer there is never a row to resolve.
Both halves are therefore exposed here.

SCHEMA
------
The experiments own causal_edge + planning_prediction (009_causal_edges.sql,
additive per task amendment_54837 — 001-008 untouched). The live edge store is
ralph_causal_edges (jsonb). `project_live_edges` copies live edges into the
experimental table so the planning check runs on REAL mined edges instead of
fixtures. The projection is one-way and idempotent; it never writes back.

DOCTRINE — stated precisely, because the imprecise version was wrong
-------------------------------------------------------------------
This adapter is NOT record-only, and an earlier draft of this docstring claimed
it was. T6's resolve path decrements support_count and can set falsified_by. Those
are writes. What is true:

  - the INVESTIGATION is held: every emitted packet carries all-False may_* authority
    and requires manager review (DR12 — propose, never auto-promote),
  - the WRITES are confined to the experimental projection (causal_edge /
    planning_prediction). ralph_causal_edges — the live store the loop actually
    reads — is never modified by this path,
  - therefore T6 does not yet feed back into live principles. It runs against a
    shadow of them. Closing that loop is a separate, explicitly-gated decision.

KNOWN LIMITS (raised by pasa-4863-review-ralph-work, not yet resolved)
---------------------------------------------------------------------
  - the projection collapses the live identity (cause, effect, scope) onto
    (source_action, direct_effect), so two edges differing only by scope conflate;
  - mission_measure is set to the direct effect, which loses the second claim;
  - support_count / evidence / predicted_certainty are not carried across faithfully;
  - the projection is SELECT-then-write with no unique constraint, so concurrent
    pre-act calls can still duplicate an edge;
  - ensure_schema applies DDL on first call rather than being an explicit,
    gated deployment step.
None of these are resolved. Do not read this module as a finished live bridge.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger("aq.management.experiments_causal")

_DB_ENV_ORDER = ("AQ_MGMT_DB_URL", "AQ_DB_URL")

# Where the experimental modules live. Added to sys.path lazily, never at import.
_EXPERIMENTS_SUBPATH = ("experiments", "causal_edges")


class ExperimentsUnavailable(RuntimeError):
    """The experimental causal track cannot run here (no DB, no driver, no schema).

    Raised so callers can degrade to a clean skip. This is NOT an error condition
    for the loop — the cycle is already durably recorded before any of this runs.
    """


def packet_authority() -> dict[str, bool]:
    """The authority block every emitted surprise packet carries. All may_* False.

    Duplicated here (rather than imported) so the contract is assertable without
    importing the experimental tree or requiring psycopg2. Kept in lockstep with
    surprise_wiring.SurprisePacket.authority and surprise_resolution's intent.
    """
    return {
        "may_dispatch_workers": False,
        "may_call_providers": False,
        "may_mutate_scheduler": False,
        "may_mutate_production": False,
        "may_accept_model_update": False,
        "requires_manager_review": True,
    }


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _dsn() -> str:
    for env in _DB_ENV_ORDER:
        value = os.environ.get(env)
        if value:
            return value
    raise ExperimentsUnavailable("no AQ_MGMT_DB_URL / AQ_DB_URL configured")


def _load_experiments():
    """Lazily import the experimental modules. Raises ExperimentsUnavailable."""
    import sys

    path = os.path.join(_repo_root(), *_EXPERIMENTS_SUBPATH)
    if not os.path.isdir(path):
        raise ExperimentsUnavailable(f"experiments dir absent: {path}")
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        import planning_check
        import surprise_wiring
    except ImportError as exc:  # psycopg2 absent, etc.
        raise ExperimentsUnavailable(f"experimental modules unimportable: {exc}") from exc
    return planning_check, surprise_wiring


def _connect():
    try:
        import psycopg2
    except ImportError as exc:
        raise ExperimentsUnavailable(f"psycopg2 unavailable: {exc}") from exc
    try:
        return psycopg2.connect(_dsn())
    except Exception as exc:  # driver-specific; treat any connect failure as unavailable
        raise ExperimentsUnavailable(f"cannot connect: {exc}") from exc


def _schema_present(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name IN ('causal_edge','planning_prediction')"
        )
        return int(cur.fetchone()[0]) == 2


def ensure_schema(conn) -> bool:
    """Apply 009_causal_edges.sql if the experimental tables are absent.

    Additive only: the migration creates causal_edge + planning_prediction and
    touches nothing that 001-008 own. Returns True when the schema is usable.
    """
    if _schema_present(conn):
        return True
    migration = os.path.join(_repo_root(), *_EXPERIMENTS_SUBPATH, "009_causal_edges.sql")
    if not os.path.isfile(migration):
        return False
    with open(migration, encoding="utf-8") as handle:
        sql = handle.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    log.info("applied additive experimental migration 009_causal_edges.sql")
    return _schema_present(conn)


def project_live_edges(conn, edges: list[dict[str, Any]]) -> int:
    """Copy live ralph_causal_edges rows into the experimental causal_edge table.

    One-way and idempotent: an edge already projected (same source_action +
    direct_effect) is refreshed in place, never duplicated, and a falsified edge
    is left falsified — a projection must not silently resurrect a demoted edge.

    Returns the number of edges projected.
    """
    if not edges:
        return 0
    projected = 0
    with conn.cursor() as cur:
        for edge in edges:
            cause = str(edge.get("cause") or "").strip()
            effect = str(edge.get("effect") or "").strip()
            if not cause or not effect:
                continue
            formality = _formality_weight(edge)
            strictness = _dial(edge.get("strictness"), _STRICTNESS_WEIGHT)
            directness = _dial(edge.get("directness"), _DIRECTNESS_WEIGHT)
            # 009_causal_edges.sql declares no unique key on (source_action,
            # direct_effect), and that file is the rescued artifact now on main —
            # it is not ours to alter. So idempotency is enforced here: look up
            # first, then update or insert. A falsified edge is deliberately left
            # untouched; re-projecting must never resurrect a demoted edge.
            cur.execute(
                "SELECT edge_id, falsified_by FROM causal_edge "
                "WHERE source_action = %s AND direct_effect = %s",
                (cause, effect),
            )
            existing = cur.fetchone()
            if existing is not None:
                if existing[1] is not None:
                    continue
                cur.execute(
                    """
                    UPDATE causal_edge
                       SET formality_weight = %s, strictness_weight = %s,
                           directness_weight = %s, updated_at = now()
                     WHERE edge_id = %s
                    """,
                    (formality, strictness, directness, existing[0]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO causal_edge
                        (source_action, direct_effect, mission_measure,
                         formality_weight, strictness_weight, directness_weight,
                         predicted_certainty, scope_conditions)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        cause, effect, effect,
                        formality, strictness, directness,
                        formality,
                        json.dumps(edge.get("scope") or {}),
                    ),
                )
            projected += 1
    conn.commit()
    return projected


# Adversarial review #3 (pasa-4863): the first version of this map invented a
# "heuristic" rung and used .25/.5/.75. The canonical ladder is defined once, in
# ralph_portable.causal_edges._FORMALITY_WEIGHT — three rungs at .34/.67/1.0.
# Import it rather than restate it, so this adapter cannot drift from the source.
from ralph_portable.causal_edges import _FORMALITY_WEIGHT as _FORMALITY_RUNGS

# Both remaining dials are CATEGORICAL in the live store, not numeric. The first
# version ran them through float(), which raised for every real value and silently
# defaulted all of them to 0.5 — destroying both dials for every projected edge.
_STRICTNESS_WEIGHT = {"advisory": 0.34, "soft": 0.67, "hard": 1.0}
_DIRECTNESS_WEIGHT = {"judgment": 0.34, "predicate": 0.67, "script": 1.0}


def _formality_weight(edge: dict[str, Any]) -> float:
    return _FORMALITY_RUNGS.get(str(edge.get("formality") or "fuzzy"), 0.34)


def _dial(value: Any, table: dict[str, float], default: float = 0.34) -> float:
    """Map a categorical dial onto [0,1]. An unrecognised value takes the LOWEST rung.

    Defaulting an unknown dial to the middle (0.5) overstated confidence in an edge
    the store could not describe; the conservative rung is the honest one.
    """
    return table.get(str(value or "").strip().lower(), default)


def record_planning_prediction(
    plan_id: str,
    actions: list[str],
    *,
    rationale: str = "",
    live_edges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """T5 pre-act: record the full outcome prediction for this plan (record-only).

    Per JEPA REC 2 (decision #829) the recorded prediction is the FULL outcome
    representation — principles supported/challenged and edges stressed — not a
    scalar pass/fail. Those rows are what T6 later resolves.
    """
    planning_check, _ = _load_experiments()
    conn = _connect()
    try:
        if not ensure_schema(conn):
            raise ExperimentsUnavailable("experimental schema unavailable")
        projected = project_live_edges(conn, live_edges or [])
        plan = planning_check.Plan(plan_id=plan_id, actions=list(actions), rationale=rationale)
        check = planning_check.PlanningCheck(conn)
        predictions = check.check_and_record(plan)
        return {
            "ok": True,
            "plan_id": plan_id,
            "edges_projected": projected,
            "predictions_recorded": len(predictions),
        }
    finally:
        conn.close()


def resolve_outcome(
    plan_id: str,
    actual_outcome: str,
    *,
    task_id: int = 4898,
    run_id: int | None = None,
) -> dict[str, Any]:
    """T6 post-outcome: resolve predictions into surprise, weight update, demotion.

    Returns the HELD packets. Nothing is actuated: every packet carries
    all-False may_* authority and requires manager review (DR12).
    """
    _, surprise_wiring = _load_experiments()
    conn = _connect()
    try:
        if not ensure_schema(conn):
            raise ExperimentsUnavailable("experimental schema unavailable")
        wiring = surprise_wiring.SurpriseWiring(conn)
        packets = wiring.resolve_outcome(
            plan_id, actual_outcome, task_id=task_id, evidence_run_id=run_id
        )
        # Adversarial review #1 (pasa-4863): the first version returned
        # held=true / applied=false. That was FALSE. SurpriseWiring.resolve_outcome
        # decrements support_count and can set falsified_by on the projected edge —
        # those are writes, and reporting them as "held, nothing applied" is exactly
        # the kind of confident-but-wrong status this whole subsystem exists to catch.
        # The honest split: the INVESTIGATION is held (no dispatch, no promotion,
        # manager review required), while the weight/demotion writes DID happen on
        # the experimental projection. Both facts are reported separately.
        return {
            "ok": True,
            "plan_id": plan_id,
            "actual_outcome": actual_outcome,
            "packets": [p.to_dict() for p in packets],
            "packet_count": len(packets),
            "authority": packet_authority(),
            "investigation_held": True,
            "wrote_to_projection": True,
            "projection_writes": "support_count decrement and/or falsified_by on causal_edge",
            "wrote_to_live_store": False,
            "live_store_note": (
                "writes land on the experimental causal_edge projection ONLY; "
                "ralph_causal_edges is not modified, so this does not yet feed "
                "back into the live principles"
            ),
        }
    finally:
        conn.close()


def failed_predictions_for_retrieval(limit: int = 20) -> list[dict[str, Any]]:
    """C4a -> C5 handoff: resolved-but-failed predictions, for the next retrieval signature.

    BB #2358 lists FOUR C4a failure modes; the fourth is "no re-entry of failed
    predictions into the C3 retrieval signature". Per #2330 L6, a failed
    prediction re-entering retrieval IS the abduction trigger — it is how C4a
    hands off to C5. Recording the surprise without exposing it to retrieval
    builds the detector and drops the handoff, leaving C5 silently open.

    SCOPE — do not overclaim (pasa-4863 review #7): this EXPOSES failed predictions.
    Nothing consumes them yet. Retrieval-signature construction does not read this,
    so the C4a->C5 handoff is NOT wired — this is the read side of it only.

    Read-only. Returns the failed predictions most recently resolved.
    """
    conn = _connect()
    try:
        if not ensure_schema(conn):
            return []
        import psycopg2.extras

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT p.prediction_id, p.edge_id, p.plan_id,
                       p.predicted_certainty, p.actual_outcome, p.surprise_level,
                       p.predicted_principles_supported,
                       p.predicted_principles_challenged,
                       e.source_action, e.direct_effect
                  FROM planning_prediction p
                  JOIN causal_edge e ON e.edge_id = p.edge_id
                 WHERE p.actual_outcome = 'failure'
                   AND p.surprise_level IS NOT NULL
                 ORDER BY p.surprise_level DESC, p.prediction_id DESC
                 LIMIT %s
                """,
                (int(limit),),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
