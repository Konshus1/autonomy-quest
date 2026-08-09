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
with psycopg2.connect(os.environ["AQ_DB_URL"]) as conn:
    for path in sorted(root.glob("*.sql")):
        sql = path.read_text()
        digest = hashlib.sha256(sql.encode()).hexdigest()
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print(f"[aq] schema current: {path.name} sha256:{digest[:12]}", flush=True)
