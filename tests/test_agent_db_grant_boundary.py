"""Grant boundary for the aqdb skill's aq_actor credential (#4834).

The seeded act-phase agent is UNTRUSTED. Its ``aq_actor`` role must be least-privilege /
DEFAULT-DENY on reads: it may read ONLY the business tables the skill curates (customers,
subscriptions) and must NOT be able to SELECT the authority/evidence tables (runs, work,
learnings, causal_*). A prior revision granted ``SELECT ON ALL TABLES IN SCHEMA public``, which
made the skill's "cannot reach authority/evidence tables" claim FALSE for reads. This test is the
executable proof of the tightened boundary in ``schema/999_container_role_grants.sql``.

Live-PG, gated exactly like the C4 principal controls: provide ``AQ_DBSKILL_ACTOR_DSN`` pointing at
the ``aq_actor`` role on a stack provisioned with the container schema. ``AQ_REQUIRE_DBSKILL_GRANTS=1``
converts the skip into a HARD FAILURE, so the boundary can never look verified while never running.
"""
from __future__ import annotations

import os

import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2 import sql as pg_sql
import pytest

ACTOR_DSN = os.environ.get("AQ_DBSKILL_ACTOR_DSN")

# AQ_REQUIRE_DBSKILL_GRANTS=1 CONVERTS THE SKIP INTO A HARD FAILURE (see test_c4_governance_pg.py).
_REQUIRE = os.environ.get("AQ_REQUIRE_DBSKILL_GRANTS", "").strip() in {"1", "true", "yes", "on"}
if _REQUIRE and not ACTOR_DSN:
    raise RuntimeError(
        "AQ_REQUIRE_DBSKILL_GRANTS=1 but AQ_DBSKILL_ACTOR_DSN is unset. The aq_actor read-boundary "
        "would SKIP. Provide the aq_actor DSN against a provisioned stack, or unset the gate. "
        "Refusing to report green without having run it."
    )

pytestmark = pytest.mark.skipif(
    not ACTOR_DSN, reason="set AQ_DBSKILL_ACTOR_DSN (aq_actor on a provisioned stack) for the grant boundary"
)

# Authority/evidence tables an untrusted agent must never read. These live in schema public in the
# container schema; aq_actor holds no privilege on them under the tightened grant.
AUTHORITY_TABLES = ["runs", "work", "learnings", "causal_edge"]
# The only tables the skill curates — reads here must keep working.
BUSINESS_TABLES = ["customers", "subscriptions"]


def _select_one(table: str) -> None:
    conn = psycopg2.connect(ACTOR_DSN)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(pg_sql.SQL("SELECT * FROM {} LIMIT 1").format(pg_sql.Identifier(table)))
            cur.fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize("table", AUTHORITY_TABLES)
def test_actor_cannot_read_authority_tables(table):
    """RED against the old blanket-SELECT grant (the read SUCCEEDS); GREEN after tightening."""
    with pytest.raises(pg_errors.InsufficientPrivilege):
        _select_one(table)


@pytest.mark.parametrize("table", BUSINESS_TABLES)
def test_actor_can_still_read_business_tables(table):
    _select_one(table)  # must not raise — the skill still works
