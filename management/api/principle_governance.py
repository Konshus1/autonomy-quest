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
claim is refuted in a new domain.
"""
from __future__ import annotations

import hashlib
import json
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


def classify_direction(expected: str, observed_delta: float, noise_tolerance: float = 0.0) -> str:
    """Classify evidence without letting no-change/noise masquerade as a refutation."""
    if expected not in ("increase", "decrease"):
        raise GovernanceError("expected_direction must be 'increase' or 'decrease'")
    delta = float(observed_delta)
    tolerance = float(noise_tolerance)
    if tolerance < 0:
        raise GovernanceError("noise_tolerance must be >= 0")
    if abs(delta) <= tolerance:
        return "noise"
    supports = delta > tolerance if expected == "increase" else delta < -tolerance
    return "supports" if supports else "refutes"


class PgGovernedPrincipleLifecycle:
    """Append-only PostgreSQL ledger and lifecycle operations for one causal-edge store."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._init_schema()

    def _connect(self):
        import psycopg2
        return psycopg2.connect(self._dsn)

    @staticmethod
    def identity(edge: dict[str, Any] | tuple[str, str, str]) -> tuple[str, str, str]:
        if isinstance(edge, tuple):
            return edge
        scope = edge.get("scope") or {}
        scope_key = json.dumps(scope, sort_keys=True) if isinstance(scope, dict) else str(scope)
        return str(edge.get("cause")), str(edge.get("effect")), scope_key

    def _init_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
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
                        ('mined','supports','refutes','noise','authorized')),
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
                          AND evidence_result='refutes' AND automatic)
                    )
                )
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
            """)

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
            cur.execute("SELECT id FROM causal_principle_transition WHERE cause=%s AND effect=%s "
                        "AND scope=%s ORDER BY id LIMIT 1", ident)
            prior = cur.fetchone()
            if prior:
                return int(prior[0])
            return self._insert(cur, ident, None, "provisional", "mined", env, evidence_ref,
                                "mined", False, False, mined_by)

    def shadow_guidance(self, edge: dict[str, Any] | tuple[str, str, str]) -> dict[str, Any]:
        ident = self.identity(edge)
        with self._connect() as conn, conn.cursor() as cur:
            latest = self._latest(cur, ident)
        status = latest["to_status"]
        return {"status": status, "may_influence_bounded_experiment": status != "promoted",
                "max_experiments": 1 if status != "promoted" else 0,
                "authoritative": status == "promoted", "transition_id": latest["id"]}

    def record_environment_test(self, edge: dict[str, Any] | tuple[str, str, str],
                                environment: dict[str, Any], evidence_ref: str,
                                expected_direction: str, observed_delta: float,
                                noise_tolerance: float = 0.0,
                                recorded_by: str = "aq-evaluator") -> dict[str, Any]:
        env = Environment.parse(environment)
        ident = self.identity(edge)
        result = classify_direction(expected_direction, observed_delta, noise_tolerance)
        with self._connect() as conn, conn.cursor() as cur:
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
            latest = self._latest(cur, ident, lock=True)
            if latest["to_status"] not in ("provisional", "demoted"):
                raise PromotionRefused(f"principle is already {latest['to_status']}")
            cur.execute("SELECT transitioned_by FROM causal_principle_transition WHERE cause=%s "
                        "AND effect=%s AND scope=%s AND transition_kind='mined' ORDER BY id LIMIT 1", ident)
            mined_by = cur.fetchone()[0]
            if adjudicated_by == mined_by:
                raise PromotionRefused("adjudicator must be independent of the principle miner")
            cur.execute("SELECT count(DISTINCT environment_fingerprint), count(DISTINCT environment_domain) "
                        "FROM causal_principle_transition WHERE cause=%s AND effect=%s AND scope=%s "
                        "AND transition_kind='shadow_test' AND evidence_result='supports'", ident)
            environment_count, domain_count = cur.fetchone()
            if environment_count < 2 or domain_count < 2:
                raise PromotionRefused("promotion requires supporting tests in >=2 environment ids and >=2 domains")
            return self._insert(cur, ident, latest["to_status"], "promoted", "promote", env,
                                evidence_ref, "authorized", False, True, adjudicated_by,
                                adjudicated_by=adjudicated_by, negative_control=negative_control,
                                negative_control_result=negative_control_result,
                                detail={"applies_here": True, "applies_here_how": applies_here_how,
                                        "supporting_environment_count": environment_count,
                                        "supporting_domain_count": domain_count,
                                        "supporting_domains": self._supporting_domains(cur, ident),
                                        "supporting_fingerprints": self._supporting_fingerprints(cur, ident)})

    def history(self, edge: dict[str, Any] | tuple[str, str, str]) -> list[dict[str, Any]]:
        ident = self.identity(edge)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id,from_status,to_status,transition_kind,environment_id,"
                        "environment_domain,environment_fingerprint,evidence_ref,evidence_result,authority_after,transitioned_by,"
                        "adjudicated_by,negative_control,negative_control_result,rule_version,automatic,created_at "
                        "FROM causal_principle_transition WHERE cause=%s AND effect=%s AND scope=%s ORDER BY id", ident)
            names = [d.name for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

    def _supporting_domains(self, cur, ident: tuple[str, str, str]) -> list[str]:
        cur.execute("SELECT DISTINCT environment_domain FROM causal_principle_transition "
                    "WHERE cause=%s AND effect=%s AND scope=%s AND transition_kind='shadow_test' "
                    "AND evidence_result='supports' ORDER BY environment_domain", ident)
        return [r[0] for r in cur.fetchall()]

    def _supporting_fingerprints(self, cur, ident: tuple[str, str, str]) -> list[str]:
        cur.execute("SELECT DISTINCT environment_fingerprint FROM causal_principle_transition "
                    "WHERE cause=%s AND effect=%s AND scope=%s AND transition_kind='shadow_test' "
                    "AND evidence_result='supports' ORDER BY environment_fingerprint", ident)
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
