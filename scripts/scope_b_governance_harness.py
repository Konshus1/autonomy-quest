#!/usr/bin/env python3
"""Scope-B exact-Compose governance acceptance controls.

This is deliberately not a pytest module.  The Compose wrapper supplies five real DSNs from a
fresh isolated stack; a missing DSN, wrong principal, missing function, or failed assertion exits
non-zero.  There is no skip or local-user fallback path.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DSN_ENV = {
    "owner": "AQ_SCOPE_B_OWNER_DSN",
    "loop": "AQ_SCOPE_B_LOOP_DSN",
    "actor": "AQ_SCOPE_B_ACTOR_DSN",
    "governance": "AQ_SCOPE_B_GOVERNANCE_DSN",
    "evaluator": "AQ_SCOPE_B_EVALUATOR_DSN",
}
GOVERNANCE_FUNCTIONS = {
    "register_flagship_causal_proposal()",
    "stage_flagship_causal_proposal(bigint,bigint,text,text,text,text,text,text,real,text)",
    "resolve_flagship_causal_edge(bigint,bigint,bigint,boolean)",
    "aq_control.attest_acquisition_evidence(bigint,bigint,integer)",
    "aq_control.attest_prediction_resolution(bigint,bigint)",
    "aq_control.register_execution_context(text,text,text,text,text,jsonb)",
    "aq_control.attest_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb)",
    "aq_control.promote_grounded_principle(text,text,text,uuid,text)",
}
EXPECTED_FUNCTION_OWNER = "aq_control_owner"
BASELINE_BLOCKING_CHECKS = {
    "causal_principle_plan_usage_governed_check": "CHECK (governed)",
    "causal_principle_plan_usage_selected_check": "CHECK (selected)",
}
EXPECTED_RED_CONSTRAINT = "causal_principle_plan_usage_governed_check"


class ReceiptControlRed(RuntimeError):
    """The harness reached the target receipt and the database rejected its content."""

    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__(str(payload.get("message") or "receipt control red"))
        self.payload = payload


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"M0 configuration error: required {name} is missing")
    return value


def connect_as(dsn: str, expected_user: str) -> dict[str, object]:
    try:
        conn = psycopg2.connect(dsn, connect_timeout=5)
    except Exception as exc:
        raise RuntimeError(f"M0 connection error for required principal {expected_user}: {exc}") from exc
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_user, current_database(), "
                "current_setting('server_version_num')::int, "
                "inet_server_addr()::text, inet_server_port(), pg_postmaster_start_time()::text"
            )
            (current_user, database, server_version_num, server_address,
             server_port, server_started_at) = cur.fetchone()
            if current_user != expected_user:
                raise AssertionError(
                    f"M0 principal mismatch: expected {expected_user}, connected as {current_user}"
                )
    return {
        "connection": conn,
        "current_user": current_user,
        "database": database,
        "server_version_num": server_version_num,
        "server_address": server_address,
        "server_port": server_port,
        "server_started_at": server_started_at,
    }


def function_inventory(owner_conn, expected_owner: str) -> list[dict[str, object]]:
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.oid::regprocedure::text,
                   pg_get_userbyid(p.proowner),
                   p.prosecdef,
                   coalesce(p.proconfig, ARRAY[]::text[])
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname IN ('public','aq_control')
               AND p.oid::regprocedure::text = ANY(%s)
             ORDER BY 1
            """,
            (sorted(GOVERNANCE_FUNCTIONS),),
        )
        rows = cur.fetchall()
    inventory = [
        {
            "function": row[0],
            "owner": row[1],
            "security_definer": row[2],
            "settings": list(row[3]),
        }
        for row in rows
    ]
    found = {row["function"] for row in inventory}
    if found != GOVERNANCE_FUNCTIONS:
        raise AssertionError(
            f"M0 function inventory mismatch: missing={sorted(GOVERNANCE_FUNCTIONS-found)} "
            f"unexpected={sorted(found-GOVERNANCE_FUNCTIONS)}"
        )
    wrong_owner = [row for row in inventory if row["owner"] != expected_owner]
    if wrong_owner:
        raise AssertionError(
            f"M0 function-owner mismatch: expected {expected_owner}, rows={wrong_owner}"
        )
    not_definers = [row["function"] for row in inventory if not row["security_definer"]]
    if not_definers:
        raise AssertionError(f"M0 required SECURITY DEFINER functions are invoker functions: {not_definers}")
    owner_conn.rollback()
    return inventory


def usage_check_inventory(conn) -> list[dict[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT conname, pg_get_constraintdef(oid)
              FROM pg_constraint
             WHERE conrelid='public.causal_principle_plan_usage'::regclass
               AND contype='c'
             ORDER BY conname
            """
        )
        rows = [{"name": name, "definition": definition} for name, definition in cur.fetchall()]
    conn.rollback()
    return rows


def insert_transition(cur, *, cause: str, from_status: str | None, to_status: str,
                      kind: str, environment_id: str, domain: str, fingerprint: str,
                      evidence_ref: str, evidence_result: str, authority_after: bool,
                      transitioned_by: str, bounded: bool = False,
                      adjudicated_by: str | None = None, automatic: bool = False,
                      detail: dict | None = None) -> int:
    cur.execute(
        """
        INSERT INTO causal_principle_transition(
          cause,effect,scope,from_status,to_status,transition_kind,
          environment_id,environment_domain,environment_fingerprint,mission_id,harness,
          evidence_ref,evidence_result,bounded_experiment,authority_after,transitioned_by,
          adjudicated_by,negative_control,negative_control_result,rule_version,automatic,detail)
        VALUES (%s,'m0-effect','{}',%s,%s,%s,%s,%s,%s,'m0-mission','m0-harness-v1',
                %s,%s,%s,%s,%s,%s,%s,%s,'aq-governed-principle-v1',%s,%s::jsonb)
        RETURNING id
        """,
        (
            cause, from_status, to_status, kind, environment_id, domain, fingerprint,
            evidence_ref, evidence_result, bounded, authority_after, transitioned_by,
            adjudicated_by,
            "negative control executed" if kind == "promote" else None,
            "expected rejection observed" if kind == "promote" else None,
            automatic, json.dumps(detail or {}),
        ),
    )
    return int(cur.fetchone()[0])


def receipt_false_case(governance_conn, checks: list[dict[str, str]]) -> dict[str, object]:
    """Demand one exact false/false usage receipt, with transactional fixture cleanup.

    Fixture construction and precondition checks deliberately sit outside the target-error catch.
    Only the final INSERT may become the expected baseline red, and only for the exact constraint
    and SQLSTATE recorded below. Every other database error is a harness error.
    """
    tag = "m0_false_receipt_" + uuid.uuid4().hex
    plan_id = f"m0-plan:{uuid.uuid4()}"
    evidence_ref = f"{tag}:negative-disposition"
    attempted = {
        "cause": tag,
        "effect": "m0-effect",
        "scope": "{}",
        "plan_id": plan_id,
        "goal_id": "m0-goal",
        "selected": False,
        "governed": False,
        "environment_id": "m0-execution",
        "environment_domain": "m0-domain",
        "environment_fingerprint": "m0-fingerprint",
        "selection_evidence_ref": evidence_ref,
    }
    governance_conn.rollback()

    # Any error here is an ERROR (exit 2), never the expected content-level red.
    with governance_conn.cursor() as cur:
        insert_transition(
            cur,
            cause=tag,
            from_status=None,
            to_status="provisional",
            kind="mined",
            environment_id="m0-mine",
            domain="m0-mine-domain",
            fingerprint="m0-mine-fp",
            evidence_ref=f"{tag}:mine",
            evidence_result="mined",
            authority_after=False,
            transitioned_by="m0-miner",
        )
        for index in (1, 2):
            insert_transition(
                cur,
                cause=tag,
                from_status="provisional",
                to_status="provisional",
                kind="shadow_test",
                environment_id=f"m0-support-{index}",
                domain=f"m0-domain-{index}",
                fingerprint=f"m0-fingerprint-{index}",
                evidence_ref=f"{tag}:support:{index}",
                evidence_result="supports",
                authority_after=False,
                transitioned_by="m0-evaluator",
                bounded=True,
            )
        promotion_id = insert_transition(
            cur,
            cause=tag,
            from_status="provisional",
            to_status="promoted",
            kind="promote",
            environment_id="m0-authorize",
            domain="m0-authorize-domain",
            fingerprint="m0-authorize-fingerprint",
            evidence_ref=f"{tag}:promote",
            evidence_result="authorized",
            authority_after=True,
            transitioned_by="m0-promoter",
            adjudicated_by="m0-independent-adjudicator",
            detail={"applies_here": True, "applies_here_how": "M0 exact fixture"},
        )
        cur.execute(
            """
            SELECT cause, effect, scope, from_status, to_status, transition_kind,
                   authority_after, adjudicated_by, automatic
              FROM causal_principle_transition
             WHERE id=%s
            """,
            (promotion_id,),
        )
        promotion_row = cur.fetchone()
        promotion_expected = (
            tag,
            "m0-effect",
            "{}",
            "provisional",
            "promoted",
            "promote",
            True,
            "m0-independent-adjudicator",
            False,
        )
        if promotion_row != promotion_expected:
            raise AssertionError(
                f"M0 promotion precondition mismatch: expected={promotion_expected!r} "
                f"returned={promotion_row!r}"
            )

    # This is the only statement whose 23514 can be classified as the expected M0 red.
    try:
        with governance_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO causal_principle_plan_usage(
                  cause,effect,scope,promotion_transition_id,plan_id,goal_id,
                  selected,governed,environment_id,environment_domain,
                  environment_fingerprint,selection_evidence_ref)
                VALUES (%s,'m0-effect','{}',%s,%s,'m0-goal',false,false,
                        'm0-execution','m0-domain','m0-fingerprint',%s)
                RETURNING id, cause, effect, scope, plan_id, goal_id, selected, governed,
                          environment_id, environment_domain, environment_fingerprint,
                          selection_evidence_ref
                """,
                (tag, promotion_id, plan_id, evidence_ref),
            )
            row = cur.fetchone()
            columns = [description.name for description in cur.description]
    except psycopg2.Error as exc:
        governance_conn.rollback()
        checks_by_name = {item["name"]: item["definition"] for item in checks}
        red_is_exact = (
            exc.pgcode == "23514"
            and getattr(exc.diag, "constraint_name", None) == EXPECTED_RED_CONSTRAINT
            and checks_by_name.get(EXPECTED_RED_CONSTRAINT)
            == BASELINE_BLOCKING_CHECKS[EXPECTED_RED_CONSTRAINT]
            and all(checks_by_name.get(name) == definition
                    for name, definition in BASELINE_BLOCKING_CHECKS.items())
        )
        if not red_is_exact:
            raise AssertionError(
                "M0 target INSERT failed for a non-accepted reason: "
                f"sqlstate={exc.pgcode!r} "
                f"constraint={getattr(exc.diag, 'constraint_name', None)!r} "
                f"checks={checks_by_name!r} message={str(exc).strip()!r}"
            ) from exc
        with governance_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM causal_principle_transition WHERE cause=%s", (tag,)
            )
            rollback_rows = int(cur.fetchone()[0])
        governance_conn.rollback()
        if rollback_rows != 0:
            raise AssertionError(f"M0 fixture rollback left {rollback_rows} transition rows")
        payload = {
            "event": "scope_b_m0_red",
            "case": "receipt_false_representability",
            "attempted": attempted,
            "promotion_precondition": list(promotion_expected),
            "usage_checks": checks,
            "sqlstate": exc.pgcode,
            "constraint": getattr(exc.diag, "constraint_name", None),
            "constraint_definition": checks_by_name[EXPECTED_RED_CONSTRAINT],
            "message": str(exc).strip(),
            "rollback_transition_rows": rollback_rows,
            "result": "RED",
        }
        raise ReceiptControlRed(payload) from exc

    returned = dict(zip(columns, row))
    for key, value in attempted.items():
        if returned[key] != value:
            governance_conn.rollback()
            raise AssertionError(
                f"M0 negative receipt content mismatch for {key}: "
                f"expected {value!r}, returned {returned[key]!r}"
            )
    governance_conn.rollback()
    with governance_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM causal_principle_transition WHERE cause=%s", (tag,))
        rollback_rows = int(cur.fetchone()[0])
    governance_conn.rollback()
    if rollback_rows != 0:
        raise AssertionError(f"M0 fixture rollback left {rollback_rows} transition rows")
    return {
        "case": "receipt_false_representability",
        "attempted": attempted,
        "promotion_precondition": list(promotion_expected),
        "returned": returned,
        "rollback_transition_rows": rollback_rows,
        "result": "PASS",
    }


def main() -> int:
    probe_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    target_commit = os.environ.get("AQ_SCOPE_B_TARGET_COMMIT", "<missing>")
    connected: dict[str, dict[str, object]] = {}
    try:
        target_commit = required_env("AQ_SCOPE_B_TARGET_COMMIT")
        dsns = {role: required_env(env_name) for role, env_name in REQUIRED_DSN_ENV.items()}
        for role, dsn in dsns.items():
            expected_user = "aq_owner" if role == "owner" else f"aq_{role}"
            connected[role] = connect_as(dsn, expected_user)
        databases = {str(info["database"]) for info in connected.values()}
        if databases != {"aq"}:
            raise AssertionError(f"M0 DSNs do not target exact Compose database aq: {databases}")
        server_identities = {
            (
                str(info["server_address"]),
                int(info["server_port"]),
                str(info["server_started_at"]),
                int(info["server_version_num"]),
            )
            for info in connected.values()
        }
        if len(server_identities) != 1:
            raise AssertionError(
                f"M0 DSNs do not target the same fresh Compose server: {server_identities}"
            )
        owner_conn = connected["owner"]["connection"]
        functions = function_inventory(owner_conn, EXPECTED_FUNCTION_OWNER)
        checks = usage_check_inventory(owner_conn)
        principal_receipt = {
            role: {key: value for key, value in info.items() if key != "connection"}
            for role, info in connected.items()
        }
        print(json.dumps({
            "event": "scope_b_m0_preflight",
            "target_commit": target_commit,
            "probe_sha256": probe_sha256,
            "principals": principal_receipt,
            "functions": functions,
            "usage_checks": checks,
        }, sort_keys=True), flush=True)
        # Receipt representability is a schema-content probe. The offline owner builds and rolls
        # back its synthetic promotion fixture; runtime promoter raw transition DML stays revoked.
        result = receipt_false_case(owner_conn, checks)
        print(json.dumps({
            "event": "scope_b_m0_control",
            "target_commit": target_commit,
            "probe_sha256": probe_sha256,
            **result,
        }, sort_keys=True), flush=True)
        return 0
    except ReceiptControlRed as exc:
        print(json.dumps({
            "target_commit": target_commit,
            "probe_sha256": probe_sha256,
            **exc.payload,
        }, sort_keys=True), file=sys.stderr, flush=True)
        return 1
    except Exception as exc:
        print(json.dumps({
            "event": "scope_b_m0_error",
            "target_commit": target_commit,
            "probe_sha256": probe_sha256,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "result": "ERROR",
        }, sort_keys=True), file=sys.stderr, flush=True)
        return 2
    finally:
        for info in connected.values():
            conn = info["connection"]
            if not conn.closed:
                conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
