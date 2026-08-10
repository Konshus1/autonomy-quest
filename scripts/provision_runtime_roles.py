#!/usr/bin/env python3
"""Idempotently provision AQ runtime principals on fresh and existing container volumes.

External database installs that supply only AQ_DB_URL remain supported and skip this container-only
step. A partially supplied credential set fails rather than creating an unusable half-boundary.
"""
from __future__ import annotations
import os
import psycopg2
from psycopg2 import sql

password_keys = {
    "aq_loop": "AQ_LOOP_DB_PASSWORD",
    "aq_actor": "AQ_ACTOR_DB_PASSWORD",
    "aq_governance": "AQ_GOVERNANCE_DB_PASSWORD",
    "aq_evaluator": "AQ_EVALUATOR_DB_PASSWORD",
}
present = {role: os.environ.get(key) for role,key in password_keys.items()}
if any(present.values()) and not all(present.values()):
    missing = [password_keys[role] for role,value in present.items() if not value]
    raise RuntimeError("partial AQ runtime role credential set; missing " + ",".join(missing))
if not any(present.values()):
    print("[aq] external database mode: runtime role provisioning skipped", flush=True)
else:
    with psycopg2.connect(os.environ["AQ_DB_URL"]) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT current_user,current_database()")
            owner, database = cur.fetchone()
            for role, password in present.items():
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
                if cur.fetchone() is None:
                    cur.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Identifier(role)))
                cur.execute(sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(role)), (password,))
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname='aq_control_owner'")
            if cur.fetchone() is None:
                cur.execute("CREATE ROLE aq_control_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE")
            cur.execute(sql.SQL("GRANT aq_control_owner TO {}").format(sql.Identifier(owner)))
            cur.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            for role in present:
                cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database),sql.Identifier(role)))
                cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
    print("[aq] runtime roles current", flush=True)
