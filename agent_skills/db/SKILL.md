---
name: aqdb
description: >-
  Read and write the business database (customers + subscriptions) through a curated,
  parameterized CLI. Use when the mission's measure is a database number — e.g. "active
  paying customers" — to inspect current state and to RECORD genuine, real results after
  doing real work. Do not fabricate data.
---

# aqdb — the business database bridge

`aqdb` is an executable on your PATH. It is your reliable, curated way to read and write the
business database. It reads the restricted database credential from the `AQ_DB_URL`
environment variable (already in your environment); you never pass credentials yourself.

Run `aqdb --help` for the exact command surface.

## The database

- `customers(id, name, email, created_at, metadata)` — the businesses/people you serve.
- `subscriptions(id, customer_id, status, monthly_amount_usd, started_at, ended_at, source, metadata)`
  — a customer's paid relationship. `status` is one of
  `trialing | active | past_due | paused | canceled`.

The canonical business measure is **active paying customers**:

```
count(distinct customer_id) from subscriptions where status = 'active'
```

An `active` subscription for a NEW customer moves that number up by one.

## Commands

| Command | What it does |
| --- | --- |
| `aqdb measure` | Print the business measure (active paying customers) + a one-line explanation. |
| `aqdb add-customer --id <id> --name <name> --email <email> [--metadata '<json>']` | Insert a customer. Idempotent: `ON CONFLICT (id) DO NOTHING`. |
| `aqdb add-subscription --customer-id <id> [--status active] [--monthly-amount <n>] [--source <s>]` | Insert a subscription for an existing customer. This is what moves the measure. |
| `aqdb list-customers [--limit N]` | List customers (read-only). |
| `aqdb query "<SELECT ...>"` | Run a single read-only `SELECT`/`WITH`. Rejects anything else. |

Add `--json` to any command for machine-readable output.

## How to use it (do real work first)

1. **Look at the current state.** `aqdb measure` and `aqdb list-customers` tell you where the
   business stands right now. `aqdb query` lets you inspect anything you can read.
2. **Do genuine work.** If the mission is to grow active paying customers, research a REAL
   prospect — a real company/segment, a real reason they'd subscribe, a defensible amount.
   The value of this system is real progress and real learnings, not invented rows.
3. **Record the real result.** Add the customer, then add their subscription:
   ```
   aqdb add-customer --id acme-co --name "Acme Co" --email ops@acme.example --metadata '{"segment":"smb"}'
   aqdb add-subscription --customer-id acme-co --status active --monthly-amount 49 --source outbound-research
   ```
4. **Confirm.** `aqdb measure` should now reflect your work.

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
