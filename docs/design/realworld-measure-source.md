# W1b — real-world / analytics measure source (honest note)

Task #6087 (child of #4834). Flag-gated, default OFF, private tree.

## What this is

The mission `measure` used to read exactly one place: the business DB, by running `measure.where`
as SQL (`Db.read_measure`). That is a number the running system can move. To prove AQ in the real
world — where it operates its own marketing site — the anti-Goodhart grade must read a REAL-WORLD
outcome (traffic / signups / conversions) from a source the acting agent **cannot write**. Otherwise
the loop grades itself against a scoreboard it can forge, and "can't lie about results" is hollow.

This adds a pluggable **measure source** seam:

- `runner/measure_source.py` — the adapter + `effective_measure_source()` resolver + the fail-closed
  event-table read.
- `Db.read_measure` (runner/db.py) — the AUTHORITATIVE, host-side, grade-time read. Dispatches on the
  effective source; the business-DB branch is byte-identical to before.
- `Measure` config (runner/config.py) — new `source` / `freshness_query` / `freshness_window`,
  validated at load (fail LOUD on a typo'd source or an external source missing its freshness query).
- `schema/032_analytics_events.sql` — the append-only `analytics_events` table + least-privilege
  grants; `schema/999` re-asserts the boundary against its own blanket grants.
- `agent_skills/db/aqmeasure` — the curated, READ-ONLY tool the agent uses to SEE the number for
  planning (advisory), documented in `agent_skills/db/SKILL.md`.

## Local-event-table vs a real analytics service

**v1 (this change) is the local event-table source, and it is complete + testable with NO external
service.** `analytics_events` is a self-hosted, append-only table the marketing site writes to. The
measure counts real events (e.g. `event_type='signup'`); freshness is `max(occurred_at)`.

**What still needs a real service / provisioning (residuals):**

1. **The site must actually emit events.** The write path — the site inserting rows via the
   `aq_site_ingest` credential — is out of scope here. Until the site emits events, the source is
   correctly UNKNOWN and the loop fails closed (see below). Nothing fabricates a number.
2. **`aq_site_ingest` role provisioning.** schema/032 grants it INSERT (append-only) only *if the
   role exists*; the deploy must create it. It is intentionally INSERT-only and has **no SELECT** —
   verified live it cannot even read back its own writes via `RETURNING`.
3. **A Plausible-style stats-API adapter** is deliberately not built. The seam is shaped for it:
   register another name in `KNOWN_SOURCES` and add a branch in `read_measure_from_source`; the
   loop/grade code does not change. That adapter would be read-only over an HTTP stats API with its
   own read-scoped credential (never one that lets the agent forge numbers).

## Where the read-only boundary is enforced (defense in depth)

1. **DB grants (the real wall).** `aq_actor` (the untrusted act-phase credential surfaced as
   `AQ_DB_URL`) has `SELECT` on `analytics_events` and **no** INSERT/UPDATE/DELETE/TRUNCATE
   (schema/032, re-asserted in schema/999 after its blanket grants). The loop's own `aq_loop` also
   has SELECT only — nothing *inside* AQ, not even the trusted loop, may write the stream. Only
   `aq_site_ingest` (the external site) appends. Proven live: `test_analytics_events_grant_boundary.py`.
2. **The tool has no write path.** `aqmeasure` exposes only `current` / `recent`, both pure SELECTs
   in a read-only session. There is no add/write command to misuse.
3. **The grade is the loop's read, not the agent's.** The agent's `aqmeasure` read is advisory. Every
   cycle the loop re-reads the same source host-side and overwrites any claimed value
   (`loop.py`: `mission_value` / `mission_delta`). A plan claiming a convenient number is graded
   against the real read.

## Fail-closed honesty

`read_measure_from_source` fails closed (raises `MeasureUnreadable`; the loop stops, credits no
progress, invents no number) when:

- the source is **unreachable** / the query errors;
- the table has **no events at all** (cannot distinguish genuine zero-traffic from an unwired /
  broken pipeline — so it will not credit a convenient zero);
- the newest event is **older than `freshness_window`** (stale / lagging / dead scoreboard).

A genuine `0` for a specific event type is trusted **only** when the pipeline is demonstrably live
(some event inside the window). Freshness is evaluated with `now()` **in the DB**, never the loop
host's wall clock.

## Flag-off = byte-identical

Default `measure.source` is `business_db` and `AQ_MEASURE_SOURCE` is unset ⇒ `read_measure` runs the
original SQL path unchanged. A stock instance.yaml (no `source:`) is unaffected. Confirmed: the full
suite is green with the flag unset (only new files + additive config/grants/dispatch).

## Proof (red → green)

- `tests/test_measure_source.py` — adapter + dispatch: business-DB byte-identical; fresh → real
  count; empty / stale / unreachable / missing-freshness / unknown-source all fail closed; the
  value always comes from the source read, never a claim; window is a bound parameter.
- `tests/test_measure_source_config.py` — default is business_db; analytics parses; unknown source
  and missing-freshness fail LOUD at load.
- `tests/test_aqmeasure_skill.py` — fresh advisory value; empty→unknown; stale→unknown; read-only
  session; no write command; only SELECTs issued.
- `tests/test_analytics_events_grant_boundary.py` — live-PG gated: `aq_actor` can SELECT, cannot
  write (every verb). Verified live on a scratch stack during build.

Live end-to-end (scratch Postgres, torn down): loop-side `read_measure` returned the real count
when fresh, ignored an attached "claim", failed closed on stale and on empty, and ran the business-DB
path byte-identically; `aq_actor` was refused every write; `aq_site_ingest` could append only.
