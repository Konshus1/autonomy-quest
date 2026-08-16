"""Pluggable measure SOURCE — where the mission's real number is read from.

The mission `measure` used to have exactly one home: the business DB, read by running
`measure.where` as SQL (see `Db.read_measure`). That is fine for "active paying customers", but it
is a number the running system itself can move. To PROVE the loop in the real world — where AQ
operates its own marketing site — the anti-Goodhart grade has to read a REAL-WORLD outcome
(traffic / signups / conversions) from a source the ACTING AGENT CANNOT WRITE. Otherwise the loop
grades itself against a scoreboard it can forge, and the whole "can't lie about results" thesis is
hollow.

This module is that seam. It is DEFAULT-OFF and byte-identical when off:

  * `effective_measure_source(measure)` returns ``"business_db"`` unless the operator opted in
    (via the `AQ_MEASURE_SOURCE` env flag, or `measure.source:` in instance.yaml). When it returns
    ``"business_db"`` the caller runs the existing SQL path UNCHANGED.
  * Only when a non-default source is selected does `read_measure_from_source` run — and it is
    strictly READ-ONLY and FAIL-CLOSED.

FAIL-CLOSED is the whole point of the honesty guarantee. If the source is unreachable, has never
received an event, or is stale beyond its freshness window, we DO NOT invent a number and DO NOT
credit progress — we raise (the loop already stops on an unreadable measure). A convenient number a
plan CLAIMS is never authority; the loop re-reads the real source here and overwrites it.

v1 adapter: ``analytics_events`` — a self-hosted, append-only events table the site writes to.
It is buildable and testable with NO external service. A Plausible-style stats-API adapter (also
read-only) can be added later by registering another name here; the loop/grade code does not change.
"""

from __future__ import annotations

import os
from decimal import Decimal

#: The default, legacy source: run `measure.where` as SQL against the business DB. Selecting this
#: (the default) means the historical read path runs UNCHANGED — this module does nothing.
BUSINESS_DB_SOURCE = "business_db"

#: v1 real-world adapter: a self-hosted append-only events table (`analytics_events`) the site
#: writes to. READ-ONLY here; the acting agent's `aq_actor` role has NO write on it (schema/032).
ANALYTICS_EVENTS_SOURCE = "analytics_events"

#: Every source name the loop knows how to read. An unknown name is a fail-closed error, never a
#: silent fall back to the business DB (which would credit a number from the WRONG source).
KNOWN_SOURCES = frozenset({BUSINESS_DB_SOURCE, ANALYTICS_EVENTS_SOURCE})

#: The external analytics adapters — everything that is NOT the legacy business-DB path. Used to
#: decide, at config-load time, whether the extra freshness config is required/meaningful.
EXTERNAL_SOURCES = frozenset({ANALYTICS_EVENTS_SOURCE})

#: The env flag. UNSET => the feature is inert and the measure read is byte-identical. When set it
#: OVERRIDES `measure.source` (mirrors AQ_WORKFLOW_SELECTOR), so an operator can flip the real-world
#: measure on/off without editing committed instance.yaml.
MEASURE_SOURCE_ENV = "AQ_MEASURE_SOURCE"

#: Default freshness window for the event-table adapter. If the newest event is older than this
#: (or there are no events at all), the source is treated as UNKNOWN and we fail closed — we cannot
#: tell "genuinely zero traffic" apart from "the ingest pipeline is broken / was never wired".
DEFAULT_FRESHNESS_WINDOW = "24 hours"


def effective_measure_source(measure) -> str:
    """The source actually in force for this measure.

    Precedence: the `AQ_MEASURE_SOURCE` env flag (operator override) wins; then `measure.source`
    from instance.yaml; then the default business-DB path. A stock instance with neither set yields
    ``"business_db"`` — the caller then runs the existing SQL read UNCHANGED (byte-identical).
    """
    env = os.environ.get(MEASURE_SOURCE_ENV)
    if env is not None and env.strip():
        return env.strip()
    configured = getattr(measure, "source", None)
    if configured and str(configured).strip():
        return str(configured).strip()
    return BUSINESS_DB_SOURCE


def is_external_source(source: str) -> bool:
    return source in EXTERNAL_SOURCES


def read_measure_from_source(measure, source: str, run_query) -> Decimal:
    """Read the mission number from a NON-business-DB source, READ-ONLY and FAIL-CLOSED.

    ``run_query(sql, args=(), one=False)`` runs a query on the loop's OWN (host-side) connection —
    this is the AUTHORITATIVE read, not the agent's advisory one. Raises the loop's
    ``MeasureUnreadable`` (imported lazily to avoid an import cycle with ``runner.db``) on any
    condition where the real number is unknown, so no progress is credited and no number is faked.
    """
    from .db import MeasureUnreadable  # lazy: db.py imports this module

    if source == ANALYTICS_EVENTS_SOURCE:
        return _read_event_table(measure, run_query, MeasureUnreadable)
    raise MeasureUnreadable(
        f"measure source {source!r} is not a known adapter ({sorted(KNOWN_SOURCES)}). "
        f"Refusing to fall back to the business DB — that would grade the mission against the "
        f"wrong scoreboard. Fix measure.source / {MEASURE_SOURCE_ENV}."
    )


def _read_event_table(measure, run_query, MeasureUnreadable) -> Decimal:
    """Read a real-world count from the append-only ``analytics_events`` table.

    Three things, in order, and every one of them fails CLOSED:

    1. LIVENESS/FRESHNESS. `measure.freshness_query` returns the timestamp of the newest event
       (e.g. ``SELECT max(occurred_at) FROM analytics_events``). We compare it to now() IN THE DB
       (never the loop's wall clock) against `measure.freshness_window`. If it is NULL (no event
       ever) or older than the window, the source is UNKNOWN — we cannot distinguish "no traffic"
       from "pipeline down / unwired" — so we raise rather than credit a convenient zero.
    2. VALUE. `measure.where` returns the counted outcome (e.g. signups). Read only after the
       pipeline is proven live, so a genuine 0 for THIS event type (with other events flowing) is
       trusted, but a 0 from a dead pipeline never is.
    3. UNREACHABLE. Any DB error at all becomes MeasureUnreadable — the loop stops instead of
       steering on a number it could not actually read.
    """
    freshness_query = (getattr(measure, "freshness_query", None) or "").strip()
    if not freshness_query:
        raise MeasureUnreadable(
            f"measure source {ANALYTICS_EVENTS_SOURCE!r} requires measure.freshness_query "
            f"(a query returning the newest event's timestamp) so liveness can be proven before "
            f"a number is credited. It is unset — failing closed."
        )
    window = (getattr(measure, "freshness_window", None) or DEFAULT_FRESHNESS_WINDOW).strip()

    # now() and the comparison run in the DB: the freshness verdict never depends on the loop host's
    # clock. The single freshness column is aliased to `latest` regardless of what it was named.
    freshness_sql = (
        "SELECT f.latest AS latest, "
        "(f.latest IS NOT NULL AND f.latest >= now() - %s::interval) AS fresh "
        f"FROM ({freshness_query}) AS f(latest)"
    )
    try:
        frow = run_query(freshness_sql, (window,), one=True)
    except Exception as e:  # unreachable / bad query / permission — all fail closed
        raise MeasureUnreadable(
            f"could not read the freshness of analytics source {ANALYTICS_EVENTS_SOURCE!r}: {e}. "
            f"Stopping rather than steering on a number we did not actually read."
        ) from e
    if not frow:
        raise MeasureUnreadable(
            f"analytics freshness query returned no rows: {freshness_query!r} — failing closed."
        )
    if frow.get("latest") is None:
        raise MeasureUnreadable(
            f"analytics source {ANALYTICS_EVENTS_SOURCE!r} has NO events — cannot tell zero-traffic "
            f"apart from an unwired/broken ingest pipeline. Not crediting a fabricated zero."
        )
    if not frow.get("fresh"):
        raise MeasureUnreadable(
            f"analytics source {ANALYTICS_EVENTS_SOURCE!r} is STALE: newest event at "
            f"{frow.get('latest')} is older than the freshness window {window!r}. Failing closed "
            f"rather than crediting a lagging or dead scoreboard."
        )

    try:
        vrow = run_query(measure.where, one=True)
    except Exception as e:
        raise MeasureUnreadable(
            f"could not read {getattr(measure, 'what', 'measure')!r} from analytics source "
            f"{ANALYTICS_EVENTS_SOURCE!r}: {e}. Stopping rather than steering on an unread number."
        ) from e
    if not vrow:
        raise MeasureUnreadable(
            f"{getattr(measure, 'what', 'measure')!r} query returned no rows: {measure.where!r}"
        )
    return Decimal(str(list(vrow.values())[0]))
