"""Governed lifecycle for AQ's own mined causal principles.

An *environment* is an independently named execution context with four required parts:
``environment_id`` (immutable run/harness instance), ``domain`` (the problem family),
``mission_id`` and ``harness`` (including its version).  Promotion requires evidence from
at least two distinct environment ids *and* two distinct domains.  Merely renaming the same
harness or repeating a run in one domain therefore cannot manufacture cross-environment proof.

Lifecycle authority is derived only from the append-only transition ledger.  There is no
status column to update without provenance: the latest transition is the state.  Provisional
and demoted principles may guide one bounded shadow experiment, but never carry authority.
Promotion is expensive and independently authorized; withdrawal is automatic when a promoted
claim is refuted in a new domain OR repeatedly governs plans without ever reaching their goal.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from typing import Any

RULE_VERSION = "aq-governed-principle-v1"
STATUSES = ("provisional", "promoted", "demoted")


class GovernanceError(ValueError):
    pass


class PromotionRefused(GovernanceError):
    pass


@dataclass(frozen=True)
class Environment:
    environment_id: str
    domain: str
    mission_id: str
    harness: str

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "Environment":
        fields = {k: str(value.get(k) or "").strip() for k in
                  ("environment_id", "domain", "mission_id", "harness")}
        missing = [k for k, v in fields.items() if not v]
        if missing:
            raise GovernanceError(f"environment requires non-empty {', '.join(missing)}")
        return cls(**fields)

    @property
    def fingerprint(self) -> str:
        # environment_id identifies a particular execution, but deliberately does NOT enter
        # the context fingerprint: two aliases for the same domain/mission/harness count once.
        canonical = json.dumps({"domain": self.domain, "mission_id": self.mission_id,
                                "harness": self.harness}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class UnproductivityPolicy:
    """Configurable patience for the common "selected but never useful" failure mode.

    Defaults require five resolved governed plans inside a 14-day rolling window, spread across
    at least 24 hours. Five repetitions reject a one-off failure while retiring a persistently
    useless rule within a normal two-week iteration; the span prevents a retry burst from stripping
    authority. Deployments with faster/slower goal cycles
    can tune all three values without changing code.
    """
    selection_threshold: int = 5
    horizon_days: int = 14
    minimum_span_days: int = 1

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "UnproductivityPolicy":
        source = env if env is not None else os.environ
        try:
            policy = cls(
                selection_threshold=int(source.get("AQ_UNPRODUCTIVE_SELECTION_THRESHOLD", "5")),
                horizon_days=int(source.get("AQ_UNPRODUCTIVE_HORIZON_DAYS", "14")),
                minimum_span_days=int(source.get("AQ_UNPRODUCTIVE_MINIMUM_SPAN_DAYS", "1")),
            )
        except (TypeError, ValueError) as exc:
            raise GovernanceError("unproductivity policy values must be integers") from exc
        if policy.selection_threshold < 3:
            raise GovernanceError("unproductivity selection threshold must be >= 3")
        if policy.horizon_days < 1:
            raise GovernanceError("unproductivity horizon must be >= 1 day")
        if not 0 <= policy.minimum_span_days <= policy.horizon_days:
            raise GovernanceError("unproductivity minimum span must be within the horizon")
        return policy



def classify_direction(expected: str, observed_delta: float, noise_tolerance: float = 0.0) -> str:
    """Classify evidence without letting no-change/noise masquerade as a refutation."""
    if expected not in ("increase", "decrease"):
        raise GovernanceError("expected_direction must be 'increase' or 'decrease'")
    delta = float(observed_delta)
    tolerance = float(noise_tolerance)
    if not math.isfinite(delta) or not math.isfinite(tolerance):
        raise GovernanceError("observed_delta and noise_tolerance must be finite")
    if tolerance < 0:
        raise GovernanceError("noise_tolerance must be >= 0")
    if abs(delta) <= tolerance:
        return "noise"
    supports = delta > tolerance if expected == "increase" else delta < -tolerance
    return "supports" if supports else "refutes"


class PgGovernedPrincipleLifecycle:
    """Append-only PostgreSQL ledger and lifecycle operations for one causal-edge store."""

    def __init__(self, dsn: str, *, init_schema: bool = True) -> None:
        self._dsn = dsn
        if init_schema:
            self._init_schema()

    def _connect(self):
        import psycopg2
        return psycopg2.connect(self._dsn)

    @staticmethod
    def identity(edge: dict[str, Any] | tuple[str, str, str]) -> tuple[str, str, str]:
        if isinstance(edge, tuple):
            return edge
        scope = edge.get("scope") or {}
        scope_key = (json.dumps(scope, sort_keys=True)
                     if isinstance(scope, dict) else str(scope))
        return str(edge.get("cause")), str(edge.get("effect")), scope_key

    def _init_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            # Exact/container installs are migration-owned. A runtime principal must not need
            # CREATE on public or replace the audited SECURITY DEFINER boundary. Only the legacy
            # standalone fallback below performs DDL when the complete migrated surface is absent.
            cur.execute("""
                SELECT to_regclass('public.causal_principle_transition'),
                       to_regclass('public.causal_principle_plan_usage'),
                       to_regclass('public.causal_principle_plan_outcome'),
                       to_regprocedure('public.validate_causal_principle_transition_insert()')
            """)
            if all(value is not None for value in cur.fetchone()):
                return
            cur.execute("""
                CREATE TABLE IF NOT EXISTS causal_principle_transition (
                    id bigserial PRIMARY KEY,
                    cause text NOT NULL CHECK (btrim(cause) <> ''),
                    effect text NOT NULL CHECK (btrim(effect) <> ''),
                    scope text NOT NULL,
                    from_status text CHECK (from_status IN ('provisional','promoted','demoted')),
                    to_status text NOT NULL CHECK (to_status IN ('provisional','promoted','demoted')),
                    transition_kind text NOT NULL CHECK (transition_kind IN
                        ('mined','shadow_test','validation','promote','demote')),
                    environment_id text NOT NULL CHECK (btrim(environment_id) <> ''),
                    environment_domain text NOT NULL CHECK (btrim(environment_domain) <> ''),
                    environment_fingerprint text NOT NULL CHECK (btrim(environment_fingerprint) <> ''),
                    mission_id text NOT NULL CHECK (btrim(mission_id) <> ''),
                    harness text NOT NULL CHECK (btrim(harness) <> ''),
                    evidence_ref text NOT NULL CHECK (btrim(evidence_ref) <> ''),
                    evidence_result text NOT NULL CHECK (evidence_result IN
                        ('mined','supports','refutes','noise','authorized','unproductive')),
                    expected_direction text CHECK (expected_direction IN ('increase','decrease')),
                    observed_delta double precision,
                    bounded_experiment boolean NOT NULL DEFAULT false,
                    authority_after boolean NOT NULL,
                    transitioned_by text NOT NULL CHECK (btrim(transitioned_by) <> ''),
                    adjudicated_by text,
                    negative_control text,
                    negative_control_result text,
                    rule_version text NOT NULL CHECK (btrim(rule_version) <> ''),
                    automatic boolean NOT NULL DEFAULT false,
                    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE (cause, effect, scope, transition_kind, evidence_ref),
                    CHECK (authority_after = (to_status = 'promoted')),
                    CHECK (
                      (transition_kind='mined' AND from_status IS NULL AND to_status='provisional'
                       AND evidence_result='mined' AND NOT automatic)
                      OR (transition_kind='shadow_test' AND from_status IN ('provisional','demoted')
                          AND to_status=from_status AND bounded_experiment AND NOT automatic)
                      OR (transition_kind='validation' AND from_status='promoted'
                          AND to_status='promoted' AND NOT bounded_experiment AND NOT automatic)
                      OR (transition_kind='promote' AND from_status IN ('provisional','demoted')
                          AND to_status='promoted' AND evidence_result='authorized' AND NOT automatic
                          AND btrim(coalesce(adjudicated_by,'')) <> ''
                          AND btrim(coalesce(negative_control,'')) <> ''
                          AND btrim(coalesce(negative_control_result,'')) <> '')
                      OR (transition_kind='demote' AND from_status='promoted' AND to_status='demoted'
                          AND evidence_result IN ('refutes','unproductive') AND automatic)
                    )
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS causal_principle_plan_usage (
                    id bigserial PRIMARY KEY,
                    cause text NOT NULL CHECK (btrim(cause) <> ''),
                    effect text NOT NULL CHECK (btrim(effect) <> ''),
                    scope text NOT NULL,
                    promotion_transition_id bigint NOT NULL REFERENCES causal_principle_transition(id),
                    plan_id text NOT NULL CHECK (btrim(plan_id) <> ''),
                    goal_id text NOT NULL CHECK (btrim(goal_id) <> ''),
                    selected boolean NOT NULL,
                    governed boolean NOT NULL,
                    environment_id text NOT NULL CHECK (btrim(environment_id) <> ''),
                    environment_domain text NOT NULL CHECK (btrim(environment_domain) <> ''),
                    environment_fingerprint text NOT NULL CHECK (btrim(environment_fingerprint) <> ''),
                    selection_evidence_ref text NOT NULL CHECK (btrim(selection_evidence_ref) <> ''),
                    selected_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE (cause,effect,scope,plan_id)
                );
                CREATE TABLE IF NOT EXISTS causal_principle_plan_outcome (
                    id bigserial PRIMARY KEY,
                    usage_id bigint NOT NULL UNIQUE REFERENCES causal_principle_plan_usage(id),
                    goal_reached boolean NOT NULL,
                    outcome_evidence_ref text NOT NULL CHECK (btrim(outcome_evidence_ref) <> ''),
                    resolved_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE OR REPLACE FUNCTION validate_causal_principle_transition_insert()
                RETURNS trigger LANGUAGE plpgsql AS $$
                DECLARE
                  latest_status text;
                  miner text;
                  evidence_floor bigint := 0;
                  execution_ids bigint;
                  contexts bigint;
                  domains bigint;
                BEGIN
                  PERFORM pg_advisory_xact_lock(hashtextextended(
                    NEW.cause || chr(31) || NEW.effect || chr(31) || NEW.scope, 0));
                  SELECT to_status INTO latest_status FROM causal_principle_transition
                    WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope
                    ORDER BY id DESC LIMIT 1;
                  IF NEW.transition_kind = 'mined' THEN
                    IF latest_status IS NOT NULL THEN
                      RAISE EXCEPTION 'mined transition requires a new principle identity';
                    END IF;
                    RETURN NEW;
                  END IF;
                  IF latest_status IS NULL OR latest_status IS DISTINCT FROM NEW.from_status THEN
                    RAISE EXCEPTION 'transition predecessor mismatch: latest %, supplied %',
                      latest_status, NEW.from_status;
                  END IF;
                  IF NEW.transition_kind = 'promote' THEN
                    SELECT transitioned_by INTO miner FROM causal_principle_transition
                      WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope
                        AND transition_kind='mined' ORDER BY id LIMIT 1;
                    IF miner IS NULL OR miner = NEW.adjudicated_by THEN
                      RAISE EXCEPTION 'promotion requires an independent adjudicator';
                    END IF;
                    SELECT coalesce(max(id),0) INTO evidence_floor FROM causal_principle_transition
                      WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope
                        AND transition_kind='demote';
                    SELECT count(DISTINCT environment_id),
                           count(DISTINCT environment_fingerprint),
                           count(DISTINCT environment_domain)
                      INTO execution_ids, contexts, domains
                      FROM causal_principle_transition
                      WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope
                        AND transition_kind='shadow_test' AND evidence_result='supports'
                        AND id > evidence_floor;
                    IF execution_ids < 2 OR contexts < 2 OR domains < 2 THEN
                      RAISE EXCEPTION 'promotion requires two cross-environment supports';
                    END IF;
                    IF coalesce(NEW.detail->>'applies_here','false') <> 'true'
                       OR btrim(coalesce(NEW.detail->>'applies_here_how','')) = '' THEN
                      RAISE EXCEPTION 'promotion requires applies_here provenance';
                    END IF;
                  END IF;
                  RETURN NEW;
                END $$;
                DROP TRIGGER IF EXISTS causal_principle_transition_validate_insert
                  ON causal_principle_transition;
                CREATE TRIGGER causal_principle_transition_validate_insert
                  BEFORE INSERT ON causal_principle_transition
                  FOR EACH ROW EXECUTE FUNCTION validate_causal_principle_transition_insert();
            """)
            cur.execute("""
                CREATE OR REPLACE FUNCTION reject_causal_principle_transition_mutation()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  RAISE EXCEPTION 'causal_principle_transition is append-only';
                END $$;
                DROP TRIGGER IF EXISTS causal_principle_transition_append_only
                  ON causal_principle_transition;
                CREATE TRIGGER causal_principle_transition_append_only
                  BEFORE UPDATE OR DELETE ON causal_principle_transition
                  FOR EACH ROW EXECUTE FUNCTION reject_causal_principle_transition_mutation();
                DROP TRIGGER IF EXISTS causal_principle_plan_usage_append_only
                  ON causal_principle_plan_usage;
                CREATE TRIGGER causal_principle_plan_usage_append_only
                  BEFORE UPDATE OR DELETE ON causal_principle_plan_usage
                  FOR EACH ROW EXECUTE FUNCTION reject_causal_principle_transition_mutation();
                DROP TRIGGER IF EXISTS causal_principle_plan_outcome_append_only
                  ON causal_principle_plan_outcome;
                CREATE TRIGGER causal_principle_plan_outcome_append_only
                  BEFORE UPDATE OR DELETE ON causal_principle_plan_outcome
                  FOR EACH ROW EXECUTE FUNCTION reject_causal_principle_transition_mutation();
            """)

    @staticmethod
    def _lock_identity(cur, ident: tuple[str, str, str]) -> None:
        # Serialize a lifecycle without mutating its append-only rows.  Locking the current row is
        # insufficient: after the waiter wakes its SELECT may retain a snapshot from before a
        # concurrent transition.  The advisory lock is acquired in its own statement, so the
        # subsequent READ COMMITTED query sees the winner's new transition.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("\x1f".join(ident),))

    def _latest(self, cur, ident: tuple[str, str, str], lock: bool = False):
        suffix = " FOR UPDATE" if lock else ""
        cur.execute("SELECT * FROM causal_principle_transition "
                    "WHERE cause=%s AND effect=%s AND scope=%s ORDER BY id DESC LIMIT 1" + suffix,
                    ident)
        row = cur.fetchone()
        if row is None:
            raise GovernanceError(f"unregistered mined principle {ident!r}")
        names = [d.name for d in cur.description]
        return dict(zip(names, row))

    def register_mined(self, edge: dict[str, Any], environment: dict[str, Any],
                       evidence_ref: str, mined_by: str) -> int:
        env = Environment.parse(environment)
        ident = self.identity(edge)
        with self._connect() as conn, conn.cursor() as cur:
            self._lock_identity(cur, ident)
            cur.execute("SELECT id FROM causal_principle_transition WHERE cause=%s AND effect=%s "
                        "AND scope=%s ORDER BY id LIMIT 1", ident)
            prior = cur.fetchone()
            if prior:
                return int(prior[0])
            return self._insert(cur, ident, None, "provisional", "mined", env, evidence_ref,
                                "mined", False, False, mined_by)

    def register_execution_context(self, environment: dict[str, Any],
                                   attestation: dict[str, Any]) -> str:
        """Register one evaluator-attested, globally namespaced execution context."""
        instance_id = str(environment.get("instance_id") or "").strip()
        execution_id = str(environment.get("environment_id") or "").strip()
        domain = str(environment.get("domain") or "").strip()
        mission_id = str(environment.get("mission_id") or "").strip()
        harness = str(environment.get("harness") or "").strip()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT aq_control.register_execution_context(%s,%s,%s,%s,%s,%s::jsonb)",
                (instance_id, execution_id, domain, mission_id, harness,
                 json.dumps(attestation)),
            )
            return str(cur.fetchone()[0])

    def attest_grounding_observation(self, edge: dict[str, Any] | tuple[str, str, str], *,
                                     context_id: str, test_kind: str, evidence_ref: str,
                                     expected_direction: str, observed_delta: float,
                                     noise_tolerance: float,
                                     evidence_payload: dict[str, Any]) -> dict[str, Any]:
        """Let only the evaluator principal append a classified, immutable observation."""
        ident = self.identity(edge)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT aq_control.attest_grounding_observation("
                "%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (context_id, *ident, test_kind, evidence_ref, expected_direction,
                 float(observed_delta), float(noise_tolerance), json.dumps(evidence_payload)),
            )
            observation_id = str(cur.fetchone()[0])
            # The evaluator can append through the checked function but cannot read or mutate the
            # private ledger directly. Promotion derives the stored classification internally.
            return {"observation_id": observation_id}

    def promote_grounded(self, edge: dict[str, Any] | tuple[str, str, str], *,
                         authorization_context_id: str,
                         review_evidence_ref: str) -> int:
        """Explicitly promote using only evaluator-owned observations derived in PostgreSQL."""
        ident = self.identity(edge)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT aq_control.promote_grounded_principle(%s,%s,%s,%s::uuid,%s)",
                (*ident, authorization_context_id, review_evidence_ref),
            )
            return int(cur.fetchone()[0])

    def authorize_plan(self, global_plan_id: str, work_id: int,
                       plan: dict[str, Any]) -> dict[str, Any]:
        """Atomically derive and persist the checked pre-ACT plan disposition."""
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT aq_control.authorize_plan(%s,%s,%s::jsonb)",
                            (global_plan_id, int(work_id), json.dumps(plan)))
                receipt = cur.fetchone()[0]
            if not isinstance(receipt, dict):
                raise GovernanceError("authorization did not return a durable receipt")
            return receipt
        except GovernanceError:
            raise
        except Exception as exc:
            raise GovernanceError(f"plan authorization rejected: {exc}") from exc

    def attest_acquisition(self, acquisition_id: int, run_id: int,
                           proposal_index: int = 0) -> int:
        """Evaluator-owned production consumer for an acquisition proposal outbox item."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT aq_control.attest_and_stage_acquisition(%s,%s,%s)",
                (int(acquisition_id), int(run_id), int(proposal_index)),
            )
            return int(cur.fetchone()[0])

    def attest_prediction_resolution(self, prediction_id: int, run_id: int) -> int:
        """Evaluator-owned production consumer for one prediction-resolution outbox item."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT aq_control.attest_and_apply_prediction(%s,%s)",
                (int(prediction_id), int(run_id)),
            )
            return int(cur.fetchone()[0])

    def shadow_guidance(self, edge: dict[str, Any] | tuple[str, str, str]) -> dict[str, Any]:
        ident = self.identity(edge)
        with self._connect() as conn, conn.cursor() as cur:
            latest = self._latest(cur, ident)
            cur.execute("SELECT id FROM causal_principle_transition WHERE cause=%s AND effect=%s "
                        "AND scope=%s AND transition_kind='promote' ORDER BY id DESC LIMIT 1", ident)
            promoted = cur.fetchone()
        status = latest["to_status"]
        return {"status": status, "may_influence_bounded_experiment": status != "promoted",
                "requires_bounded_experiment": status != "promoted",
                "authoritative": status == "promoted", "transition_id": latest["id"],
                "promotion_transition_id": int(promoted[0]) if promoted else None}

    def record_plan_use(self, edge: dict[str, Any] | tuple[str, str, str], *,
                        promotion_transition_id: int, plan_id: str, goal_id: str,
                        environment: dict[str, Any], evidence_ref: str) -> int:
        """Append a pre-ACT receipt that this exact promoted rule governs the chosen plan."""
        env = Environment.parse(environment)
        ident = self.identity(edge)
        if not str(plan_id or "").strip() or not str(goal_id or "").strip():
            raise GovernanceError("plan_id and goal_id are required for usefulness accounting")
        with self._connect() as conn, conn.cursor() as cur:
            self._lock_identity(cur, ident)
            latest = self._latest(cur, ident)
            cur.execute("SELECT id FROM causal_principle_transition WHERE cause=%s AND effect=%s "
                        "AND scope=%s AND transition_kind='promote' ORDER BY id DESC LIMIT 1", ident)
            promotion = cur.fetchone()
            if (latest["to_status"] != "promoted" or promotion is None
                    or int(promotion[0]) != int(promotion_transition_id)):
                raise GovernanceError("selection receipt does not match the current promotion")
            cur.execute("""
                INSERT INTO causal_principle_plan_usage
                  (cause,effect,scope,promotion_transition_id,plan_id,goal_id,selected,governed,
                   environment_id,environment_domain,environment_fingerprint,selection_evidence_ref)
                VALUES (%s,%s,%s,%s,%s,%s,true,true,%s,%s,%s,%s)
                ON CONFLICT (cause,effect,scope,plan_id) DO NOTHING RETURNING id
            """, (*ident, int(promotion_transition_id), str(plan_id), str(goal_id),
                  env.environment_id, env.domain, env.fingerprint, evidence_ref))
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def record_plan_outcome(self, edge: dict[str, Any] | tuple[str, str, str], *,
                            plan_id: str, goal_reached: bool, evidence_ref: str,
                            environment: dict[str, Any],
                            policy: UnproductivityPolicy | None = None) -> dict[str, Any]:
        """Append a goal outcome and demote sustained zero-usefulness under the current promotion."""
        env = Environment.parse(environment)
        ident = self.identity(edge)
        policy = policy or UnproductivityPolicy.from_env()
        with self._connect() as conn, conn.cursor() as cur:
            self._lock_identity(cur, ident)
            cur.execute("SELECT id,promotion_transition_id FROM causal_principle_plan_usage "
                        "WHERE cause=%s AND effect=%s AND scope=%s AND plan_id=%s", (*ident, str(plan_id)))
            usage = cur.fetchone()
            latest = self._latest(cur, ident)
            if usage is None:
                return {"resolved": False, "automatic_demotion": False,
                        "status": latest["to_status"], "reason": "unknown plan (no pre-ACT receipt)"}
            cur.execute("""
                INSERT INTO causal_principle_plan_outcome
                  (usage_id,goal_reached,outcome_evidence_ref)
                VALUES (%s,%s,%s) ON CONFLICT (usage_id) DO NOTHING RETURNING id
            """, (usage[0], bool(goal_reached), evidence_ref))
            resolved = cur.fetchone()
            if resolved is None:
                return {"resolved": False, "automatic_demotion": False,
                        "status": latest["to_status"], "reason": "replayed outcome"}
            if latest["to_status"] != "promoted":
                return {"resolved": True, "automatic_demotion": False,
                        "status": latest["to_status"], "reason": "principle has no authority"}
            cur.execute("SELECT id FROM causal_principle_transition WHERE cause=%s AND effect=%s "
                        "AND scope=%s AND transition_kind='promote' ORDER BY id DESC LIMIT 1", ident)
            current_promotion = int(cur.fetchone()[0])
            if int(usage[1]) != current_promotion:
                return {"resolved": True, "automatic_demotion": False, "status": "promoted",
                        "reason": "late outcome from an older promotion generation"}
            cur.execute("""
                SELECT count(*) FILTER (WHERE u.selected),
                       count(*) FILTER (WHERE u.governed),
                       count(*) FILTER (WHERE o.goal_reached),
                       extract(epoch FROM (max(o.resolved_at)-min(u.selected_at))) / 86400.0,
                       array_agg(u.id ORDER BY u.id)
                  FROM causal_principle_plan_usage u
                  JOIN causal_principle_plan_outcome o ON o.usage_id=u.id
                 WHERE u.cause=%s AND u.effect=%s AND u.scope=%s
                   AND u.promotion_transition_id=%s
                   AND u.selected_at >= now() - (%s * interval '1 day')
            """, (*ident, current_promotion, policy.horizon_days))
            selected_count, governed_count, successes, span_days, usage_ids = cur.fetchone()
            span_days = float(span_days or 0.0)
            metrics = {"selected": int(selected_count), "governed": int(governed_count),
                       "successful_chains": int(successes), "span_days": round(span_days, 4),
                       "selection_threshold": policy.selection_threshold,
                       "horizon_days": policy.horizon_days,
                       "minimum_span_days": policy.minimum_span_days,
                       "usage_ids": list(usage_ids or [])}
            should_demote = (
                selected_count >= policy.selection_threshold
                and governed_count >= policy.selection_threshold
                and successes == 0
                and span_days >= policy.minimum_span_days
            )
            if not should_demote:
                return {"resolved": True, "automatic_demotion": False,
                        "status": "promoted", "reason": "usefulness patience retained",
                        "metrics": metrics}
            tid = self._insert(
                cur, ident, "promoted", "demoted", "demote", env,
                f"{evidence_ref}#unproductivity", "unproductive", False, False,
                "aq-auto-demoter", automatic=True,
                detail={"reason": "selected and governed repeatedly with zero goal-reaching chains",
                        "trigger": "unproductivity", "promotion_transition_id": current_promotion,
                        **metrics})
            return {"resolved": True, "automatic_demotion": True, "status": "demoted",
                    "reason": "unproductive over configured horizon", "metrics": metrics,
                    "transition_id": tid}

    def record_environment_test(self, edge: dict[str, Any] | tuple[str, str, str],
                                environment: dict[str, Any], evidence_ref: str,
                                expected_direction: str, observed_delta: float,
                                noise_tolerance: float = 0.0,
                                recorded_by: str = "aq-evaluator") -> dict[str, Any]:
        env = Environment.parse(environment)
        ident = self.identity(edge)
        result = classify_direction(expected_direction, observed_delta, noise_tolerance)
        with self._connect() as conn, conn.cursor() as cur:
            self._lock_identity(cur, ident)
            # Evidence references are replay keys across every transition kind.  A retry after
            # demotion must not manufacture a second transition under the new status.
            cur.execute("SELECT id,to_status,evidence_result FROM causal_principle_transition "
                        "WHERE cause=%s AND effect=%s AND scope=%s AND evidence_ref=%s "
                        "ORDER BY id LIMIT 1", (*ident, evidence_ref))
            replay = cur.fetchone()
            if replay:
                return {"result": replay[2], "automatic_demotion": False,
                        "status": replay[1], "transition_id": replay[0], "replayed": True}
            latest = self._latest(cur, ident, lock=True)
            status = latest["to_status"]
            if status == "promoted" and result == "refutes" and self._is_new_domain(cur, ident, env):
                tid = self._insert(cur, ident, "promoted", "demoted", "demote", env,
                                   evidence_ref, result, False, False, "aq-auto-demoter",
                                   expected_direction, observed_delta, automatic=True,
                                   detail={"reason": "refutation in a domain absent from promotion evidence"})
                return {"result": result, "automatic_demotion": True, "status": "demoted",
                        "transition_id": tid}
            kind = "validation" if status == "promoted" else "shadow_test"
            tid = self._insert(cur, ident, status, status, kind, env, evidence_ref, result,
                               kind == "shadow_test", status == "promoted", recorded_by,
                               expected_direction, observed_delta)
            return {"result": result, "automatic_demotion": False, "status": status,
                    "transition_id": tid}

    def promote(self, edge: dict[str, Any] | tuple[str, str, str], *,
                authorization_environment: dict[str, Any], evidence_ref: str,
                applies_here: bool, applies_here_how: str,
                negative_control: str, negative_control_result: str,
                adjudicated_by: str) -> int:
        env = Environment.parse(authorization_environment)
        required = {"applies_here_how": applies_here_how, "evidence_ref": evidence_ref,
                    "negative_control": negative_control,
                    "negative_control_result": negative_control_result,
                    "adjudicated_by": adjudicated_by}
        missing = [k for k, v in required.items() if not str(v or "").strip()]
        if not applies_here or missing:
            raise PromotionRefused("promotion requires applies_here=true and non-empty " +
                                   ", ".join(missing or ["applies_here"]))
        ident = self.identity(edge)
        with self._connect() as conn, conn.cursor() as cur:
            self._lock_identity(cur, ident)
            latest = self._latest(cur, ident, lock=True)
            if latest["to_status"] not in ("provisional", "demoted"):
                raise PromotionRefused(f"principle is already {latest['to_status']}")
            cur.execute("SELECT transitioned_by FROM causal_principle_transition WHERE cause=%s "
                        "AND effect=%s AND scope=%s AND transition_kind='mined' ORDER BY id LIMIT 1", ident)
            mined_by = cur.fetchone()[0]
            if adjudicated_by == mined_by:
                raise PromotionRefused("adjudicator must be independent of the principle miner")
            evidence_floor = 0
            if latest["to_status"] == "demoted":
                cur.execute("SELECT max(id) FROM causal_principle_transition WHERE cause=%s AND effect=%s "
                            "AND scope=%s AND transition_kind='demote'", ident)
                evidence_floor = int(cur.fetchone()[0] or 0)
            cur.execute("SELECT count(DISTINCT environment_id), count(DISTINCT environment_fingerprint), "
                        "count(DISTINCT environment_domain) FROM causal_principle_transition "
                        "WHERE cause=%s AND effect=%s AND scope=%s AND transition_kind='shadow_test' "
                        "AND evidence_result='supports' AND id>%s", (*ident, evidence_floor))
            environment_id_count, environment_count, domain_count = cur.fetchone()
            if environment_id_count < 2 or environment_count < 2 or domain_count < 2:
                raise PromotionRefused("promotion requires supports in >=2 execution ids, canonical environments, and domains")
            return self._insert(cur, ident, latest["to_status"], "promoted", "promote", env,
                                evidence_ref, "authorized", False, True, adjudicated_by,
                                adjudicated_by=adjudicated_by, negative_control=negative_control,
                                negative_control_result=negative_control_result,
                                detail={"applies_here": True, "applies_here_how": applies_here_how,
                                        "supporting_environment_id_count": environment_id_count,
                                        "supporting_environment_count": environment_count,
                                        "supporting_domain_count": domain_count,
                                        "supporting_domains": self._supporting_domains(cur, ident, evidence_floor),
                                        "supporting_fingerprints": self._supporting_fingerprints(cur, ident, evidence_floor)})

    def history(self, edge: dict[str, Any] | tuple[str, str, str]) -> list[dict[str, Any]]:
        ident = self.identity(edge)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id,from_status,to_status,transition_kind,environment_id,"
                        "environment_domain,environment_fingerprint,evidence_ref,evidence_result,authority_after,transitioned_by,"
                        "adjudicated_by,negative_control,negative_control_result,rule_version,automatic,created_at "
                        "FROM causal_principle_transition WHERE cause=%s AND effect=%s AND scope=%s ORDER BY id", ident)
            names = [d.name for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

    def _supporting_domains(self, cur, ident: tuple[str, str, str], evidence_floor: int = 0) -> list[str]:
        cur.execute("SELECT DISTINCT environment_domain FROM causal_principle_transition "
                    "WHERE cause=%s AND effect=%s AND scope=%s AND transition_kind='shadow_test' "
                    "AND evidence_result='supports' AND id>%s ORDER BY environment_domain", (*ident, evidence_floor))
        return [r[0] for r in cur.fetchall()]

    def _supporting_fingerprints(self, cur, ident: tuple[str, str, str], evidence_floor: int = 0) -> list[str]:
        cur.execute("SELECT DISTINCT environment_fingerprint FROM causal_principle_transition "
                    "WHERE cause=%s AND effect=%s AND scope=%s AND transition_kind='shadow_test' "
                    "AND evidence_result='supports' AND id>%s ORDER BY environment_fingerprint", (*ident, evidence_floor))
        return [r[0] for r in cur.fetchall()]

    def _is_new_domain(self, cur, ident: tuple[str, str, str], env: Environment) -> bool:
        cur.execute("SELECT detail FROM causal_principle_transition WHERE cause=%s AND effect=%s AND scope=%s "
                    "AND transition_kind='promote' ORDER BY id DESC LIMIT 1", ident)
        row = cur.fetchone()
        detail = row[0] or {} if row else {}
        domains = set(detail.get("supporting_domains", []))
        fingerprints = set(detail.get("supporting_fingerprints", []))
        return env.domain not in domains and env.fingerprint not in fingerprints

    def _insert(self, cur, ident, from_status, to_status, kind, env, evidence_ref, result,
                bounded, authority, actor, expected_direction=None, observed_delta=None, *,
                adjudicated_by=None, negative_control=None, negative_control_result=None,
                automatic=False, detail=None) -> int:
        if not str(evidence_ref or "").strip() or not str(actor or "").strip():
            raise GovernanceError("evidence_ref and transitioned_by are required")
        cur.execute("""
            INSERT INTO causal_principle_transition
              (cause,effect,scope,from_status,to_status,transition_kind,environment_id,
               environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,evidence_result,
               expected_direction,observed_delta,bounded_experiment,authority_after,
               transitioned_by,adjudicated_by,negative_control,negative_control_result,
               rule_version,automatic,detail)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (*ident, from_status, to_status, kind, env.environment_id, env.domain,
              env.fingerprint, env.mission_id, env.harness, evidence_ref, result, expected_direction,
              observed_delta, bounded, authority, actor, adjudicated_by, negative_control,
              negative_control_result, RULE_VERSION, automatic, json.dumps(detail or {})))
        return int(cur.fetchone()[0])
