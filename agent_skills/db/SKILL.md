---
name: aqdb
description: >-
  Read and write the business database (customers + subscriptions) through a curated,
  parameterized CLI. Use when the mission's measure is a database number to inspect
  current state and to RECORD genuine, real results after doing real work. Do not
  fabricate data. The mission's own measure query is authoritative — not `aqdb measure`.
---

# aqdb — the business database bridge

`aqdb` is an executable on your PATH. It is your reliable, curated way to read and write the
business database. It reads the restricted database credential from the `AQ_DB_URL`
environment variable (already in your environment); you never pass credentials yourself.

Run `aqdb --help` for the exact command surface.

## The database

- `customers(id, name, email, created_at, metadata)` — a tracked company or person. Recording a
  real company you researched is honest CRM work (a prospect). The table has no separate
  prospect/customer type; "tracked prospect" is how you use the row, not a schema flag.
- `subscriptions(id, customer_id, status, monthly_amount_usd, started_at, ended_at, source, metadata)`
  — a customer's paid relationship. `status` is one of
  `trialing | active | past_due | paused | canceled`.

**YOUR mission's `measure` is the number that matters.** Read that query and run it with
`aqdb query`. `aqdb measure` is only the flagship paying-customers query
(`count(distinct customer_id) from subscriptions where status = 'active'`). Use it when the
mission actually counts that; otherwise it will disagree with the mission and you should
trust the mission.

An `active` subscription for a new customer moves the *flagship* query. It moves YOUR mission
measure only if that is what the mission counts. Do not add an `active` subscription to fake
a sale.

## Commands

| Command | What it does |
| --- | --- |
| `aqdb measure` | Print the flagship paying-customers query. Not necessarily YOUR mission measure. |
| `aqdb add-customer --id <id> --name <name> --email <email> [--metadata '<json>']` | Insert a customer / tracked prospect. Idempotent: `ON CONFLICT (id) DO NOTHING`. |
| `aqdb add-subscription --customer-id <id> [--status active] [--monthly-amount <n>] [--source <s>]` | Insert a subscription for an existing customer. Use only when a paying relationship genuinely exists. |
| `aqdb list-customers [--limit N]` | List customers (read-only). |
| `aqdb query "<SELECT ...>"` | Run a single read-only `SELECT`/`WITH`. Use this to read the mission measure. |

Add `--json` to any command for machine-readable output.

## How to use it (do real work first)

1. **Look at the current state.** Read YOUR mission's measure query. Run it with
   `aqdb query`. `aqdb list-customers` shows who is already recorded.
2. **Do genuine work.** Research a REAL company/segment. The value of this system is real
   progress and real learnings, not invented rows.
3. **Record the real result, honestly:**
   ```
   aqdb add-customer --id acme-co --name "Acme Co" --email ops@acme.example --metadata '{"segment":"smb"}'
   ```
   That is a tracked prospect. Add a subscription **only** when a paying relationship
   actually exists — do not use `--status active` to invent a sale:
   ```
   aqdb add-subscription --customer-id acme-co --status active --monthly-amount 49 --source outbound-research
   ```
4. **Confirm** with `aqdb query` of the mission's own measure SQL. `aqdb measure` confirms
   the work only when the mission is the flagship paying-customers query.

## Safety (what this tool will and will not do)

- **Curated writes only.** Writes happen exclusively through `add-customer` /
  `add-subscription`, with every value bound as a SQL parameter. There is no raw-write path.
- **`query` is strictly read-only.** It refuses statement chaining, comments, and anything
  that is not a single `SELECT`/`WITH`, and it runs inside a read-only DB session. Use the
  curated commands to write.
- **Least privilege.** The DSN is the restricted `aq_actor` role: it can read and write ONLY
  `customers` and `subscriptions`. It cannot read or write the authority/evidence tables (runs,
  work, learnings, causal_*).

## Do not fabricate

Every customer and subscription you add should correspond to genuine work you actually did.
Inventing rows to move the number is the one failure mode this whole exercise exists to
avoid. Record what is real; report honestly when progress is hard.

## `aqmeasure` — the real-world measure (READ-ONLY)

When the mission measure is a real-world outcome — traffic, signups, conversions — it lives in
the append-only `analytics_events` table the marketing site writes to, NOT in `customers` /
`subscriptions`. Use the companion tool `aqmeasure` to SEE that number for planning:

| Command | What it does |
| --- | --- |
| `aqmeasure current --event-type <t> [--window '24 hours']` | Advisory count for one event type, with a fail-closed freshness verdict (`fresh` / `stale` / `unknown`). |
| `aqmeasure recent [--event-type <t>] [--limit N]` | List recent events (read-only) so you can see what is flowing. |

Two things make this tool different from `aqdb`, and both are the point:

- **You cannot write it.** There is no add/write command, and the `aq_actor` role has SELECT and
  NO INSERT/UPDATE/DELETE on `analytics_events` (schema/032). You can read the real-world number;
  you cannot forge it. Only the external site-ingest credential writes events.
- **It is advisory, not the grade.** Every cycle the loop does its OWN host-side read of the same
  source and grades against THAT, overwriting whatever a plan claimed. Plan against `aqmeasure`
  freely; you cannot move the grade by reporting a convenient number — the loop re-reads reality.

If `aqmeasure current` says `unknown` (no events ever) or `stale` (newest event older than the
window), the real number is UNKNOWN and the loop **fails closed** — it credits no progress and
invents no number. That is not a bug to work around: get the ingest pipeline verified, or do work
that produces genuine, freshly-recorded real-world events.
