"""Reproducible grant boundary for the mission MEASURE (the un-forgeable scoreboard).

The whole honesty claim: the untrusted act-phase credential (``aq_actor``) may READ the measure
(``analytics_events``) but must NEVER write it. Otherwise the loop could grade itself against a
scoreboard it can forge. The boundary is a plain PostgreSQL GRANT (SELECT only), enforced by the
database, not a policy or a prompt. This test is the executable proof.

Reproduce it: provision the container roles, apply the schema (``schema/031_measure_source.sql`` +
``schema/999_container_role_grants.sql``), set ``AQ_DBSKILL_ACTOR_DSN`` to the ``aq_actor`` role on
that stack, and run::

    AQ_REQUIRE_DBSKILL_GRANTS=1 pytest tests/test_measure_grant_boundary.py

``aq_actor`` SELECT on ``analytics_events`` succeeds; every write returns ``permission denied``.
"""
import os

import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2 import sql as pg_sql
import pytest

ACTOR_DSN = os.environ.get("AQ_DBSKILL_ACTOR_DSN")

# AQ_REQUIRE_DBSKILL_GRANTS=1 converts the skip into a hard failure (refuse to report green unrun).
_REQUIRE = os.environ.get("AQ_REQUIRE_DBSKILL_GRANTS", "").strip() in {"1", "true", "yes", "on"}
if _REQUIRE and not ACTOR_DSN:
    raise RuntimeError(
        "AQ_REQUIRE_DBSKILL_GRANTS=1 but AQ_DBSKILL_ACTOR_DSN is unset. The measure write-boundary "
        "would SKIP. Provide the aq_actor DSN against a provisioned stack, or unset the gate."
    )

pytestmark = pytest.mark.skipif(
    not ACTOR_DSN,
    reason="set AQ_DBSKILL_ACTOR_DSN (aq_actor on a provisioned stack) for the measure boundary",
)

MEASURE = "analytics_events"

# Every way an agent could try to move its own scoreboard. All must be denied for aq_actor.
WRITE_STATEMENTS = [
    "INSERT INTO {t} (event_type) VALUES ('forged-progress')",
    "UPDATE {t} SET event_type = 'forged'",
    "DELETE FROM {t}",
    "TRUNCATE {t}",
]


def _connect():
    return psycopg2.connect(ACTOR_DSN)


def test_actor_can_read_the_measure():
    """The agent MUST be able to read the number it is graded on."""
    conn = _connect()
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(pg_sql.SQL("SELECT * FROM {} LIMIT 1").format(pg_sql.Identifier(MEASURE)))
            cur.fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize("stmt", WRITE_STATEMENTS)
def test_actor_cannot_write_the_measure(stmt):
    """The agent CANNOT write the number it is graded on: Postgres returns permission denied.

    This is the whole honesty guarantee, reduced to a database privilege check.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            with pytest.raises(pg_errors.InsufficientPrivilege):
                cur.execute(stmt.format(t=MEASURE))
    finally:
        conn.rollback()
        conn.close()
