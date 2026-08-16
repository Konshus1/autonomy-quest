"""Grant boundary for the W1b real-world measure source (task #6087).

THE thesis-critical property: the untrusted act-phase agent (``aq_actor``) can SEE the real-world
number (SELECT on ``analytics_events``, for the ``aqmeasure`` planning tool) but can NEVER WRITE it —
so it cannot forge its own scoreboard. The loop's own host-side read is the authority. This is the
executable proof of the grants in ``schema/032_analytics_events.sql`` + ``schema/999_container_role_grants.sql``.

Live-PG, gated exactly like the aqdb grant boundary: provide ``AQ_DBSKILL_ACTOR_DSN`` pointing at the
``aq_actor`` role on a stack provisioned with the container schema. ``AQ_REQUIRE_DBSKILL_GRANTS=1``
converts the skip into a HARD FAILURE, so the boundary can never look verified while never running.
"""

from __future__ import annotations

import os

import psycopg2
from psycopg2 import errors as pg_errors
import pytest

ACTOR_DSN = os.environ.get("AQ_DBSKILL_ACTOR_DSN")

_REQUIRE = os.environ.get("AQ_REQUIRE_DBSKILL_GRANTS", "").strip() in {"1", "true", "yes", "on"}
if _REQUIRE and not ACTOR_DSN:
    raise RuntimeError(
        "AQ_REQUIRE_DBSKILL_GRANTS=1 but AQ_DBSKILL_ACTOR_DSN is unset. The analytics_events "
        "read-only boundary would SKIP. Provide the aq_actor DSN against a provisioned stack, or "
        "unset the gate. Refusing to report green without having run it."
    )

pytestmark = pytest.mark.skipif(
    not ACTOR_DSN,
    reason="set AQ_DBSKILL_ACTOR_DSN (aq_actor on a provisioned stack) for the analytics grant boundary",
)


def test_actor_can_read_analytics_events():
    """aq_actor MUST be able to SELECT it — the aqmeasure planning tool depends on this."""
    conn = psycopg2.connect(ACTOR_DSN)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM analytics_events")
            cur.fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO analytics_events (event_type) VALUES ('signup')",
        "UPDATE analytics_events SET event_type='x'",
        "DELETE FROM analytics_events",
        "TRUNCATE analytics_events",
    ],
)
def test_actor_cannot_write_analytics_events(statement):
    """The forge-the-scoreboard boundary: every write verb is refused at the DB."""
    conn = psycopg2.connect(ACTOR_DSN)
    try:
        with conn.cursor() as cur:
            with pytest.raises(pg_errors.InsufficientPrivilege):
                cur.execute(statement)
        conn.rollback()
    finally:
        conn.close()
