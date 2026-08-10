#!/usr/bin/env python3
"""Apply every idempotent AQ schema file to a reachable database on app startup.

Postgres init hooks run only for an empty volume. This pass keeps an existing named volume on the
same schema as the rebuilt app; it never seeds business content.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import psycopg2

root = Path(__file__).resolve().parents[1] / "schema"
# Init hooks do not run on an existing named volume. Provision roles before any SQL migration so
# owner/ACL blocks cannot silently skip and preserve an older runtime bypass.
exec((Path(__file__).with_name("provision_runtime_roles.py")).read_text(), {"__name__": "__main__"})
with psycopg2.connect(os.environ["AQ_DB_URL"]) as conn:
    for path in sorted(root.glob("*.sql")):
        sql = path.read_text()
        digest = hashlib.sha256(sql.encode()).hexdigest()
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print(f"[aq] schema current: {path.name} sha256:{digest[:12]}", flush=True)
