# Docker Compose: the complete local stack

A clean checkout starts the database and application with one command:

```sh
scripts/compose-with-secrets.sh up
```

Then open **http://localhost:8090**. The status-only UI is at
**http://localhost:8080**.

The stack deliberately separates six services:

| service | contents | host ports |
|---|---|---|
| `postgres` | pinned PostgreSQL 16 + Apache AGE + pgvector | none by default |
| `migrate` | one-shot schema owner; exits before runtime starts | none |
| `governance` | separately credentialed lifecycle writer/API | 8091 (loopback) |
| `act-worker` | Codex plus only the restricted actor DB principal and workspace | none |
| `loop` | deterministic mission orchestration; no Codex home or workspace | none |
| `app` | device-login/API/status surfaces; mission loop disabled | 8090, 8080 |

`EXPOSE` does not publish a port; the host mappings live in `docker-compose.yml`. PostgreSQL
port 5432 stays on the Compose network. For local database development only, use:

```sh
scripts/compose-with-secrets.sh -f docker-compose.yml -f docker-compose.dev.yml up
```

## First start and account connection

The public image contains **no API keys or account credentials**. At first start the management
UI shows **Connect your account**. It launches Codex's native ChatGPT device authorization:

1. click **Connect your account**;
2. open the displayed URL on any device and enter the short code;
3. the container polls until `codex login status` succeeds.

There is no OAuth callback listener and no extra port to publish. Codex stores the resulting
credential in the `aq-codex-auth` named volume, outside the image.

For a developer who is already logged in on the host, the explicit test-only path is:

```sh
scripts/compose-with-secrets.sh -f docker-compose.yml -f docker-compose.dev-auth.yml up
```

That override mounts `${HOME}/.codex/auth.json` read-only. Never publish an image or Compose
bundle containing that file.

## Container, process, and credential boundaries

All runtime services are unprivileged, capability-free, `no-new-privileges` containers with a
root-owned immutable `/app`. Only `act-worker` mounts `/workspace` and Codex authentication. Codex
0.147+ is pinned, and `/etc/codex/requirements.toml` makes the subscription credential,
`/run/secrets`, proc environments/memory, and their proc-root aliases unreadable to every
model-generated tool process. Core dumps are disabled. The device-login app can update the shared
auth volume, but it never executes a model.

The loop reaches ACT over a narrow RPC network. Its random HMAC key is a Docker secret file, not a
process environment variable, and that file is covered by Codex's enforced read-deny policy. Each
request binds its body to a 30-second timestamp and one-time nonce; the worker rejects replay.
A body-bound request ID has a durable receipt in `aq-act-receipts`: completed responses are returned
idempotently, while a crash, lost response, or post-dispatch rate limit freezes the work for human
reconciliation instead of retrying a possible side effect. The actor network cannot resolve or reach governance. ACT receives only `aq_actor` as `AQ_DB_URL`;
PostgreSQL rejects its causal, run, and lifecycle writes.

The loop holds `aq_loop` and a narrowly scoped loop-writer token. That token can obtain pre-ACT
selection receipts, submit completed-run-backed provisional proposals, and ask the writer to close
a usage only after an exact durable ACT receipt. It cannot call the separate environment-evidence
or promotion routes. `aq_governance` independently re-reads the configured mission measure and
alone appends governed usage outcomes. The separate evidence token is reserved for trusted
external evaluation; the promotion token/adjudicator remains a distinct human/CI gate. `aq_owner`
exists only in `migrate`.

`workspace-write` remains mandatory; never use Codex's dangerous sandbox bypass. This is a
single-user, loopback-only stack, not a hostile multi-tenant service.

## Fresh database contents and flagship mission

On a new `aq-postgres-data` volume, Postgres initialization applies every file in `schema/`:

- the loop, history, heartbeat, causal-edge, business-benchmark, gate, and process-liveness tables;
- `CREATE EXTENSION age` and `CREATE EXTENSION vector`;
- the AGE graph `autonomy_quest`;
- the 21 canonical active frame dimensions;
- an empty causal-principle corpus;
- empty `customers` and `subscriptions` tables.

**Structure is seeded; content is not.** There are zero invented customers, subscriptions,
learnings, episodes, or fleet-specific principles. The exact flagship measure therefore starts at
a truthful `0`, read by running:

```sql
select count(distinct customer_id) from subscriptions where status='active';
```

The app image carries the running-a-business benchmark mission: **Get to 20 paying customers by
the end of Q3**, `reach_and_maintain`. Once subscription login completes, the loop starts pursuing
it. At `http://localhost:8090` the first four cards show the measure over time, what each cycle did
and why, the evidence-linked learning trail, and the exceptional gate queue. The queue should
usually be empty; the UI says explicitly that this means ordinary work is proceeding autonomously.

To run an interviewed mission instead, override the flagship explicitly:

```sh
AQ_INSTANCE_FILE=/absolute/path/to/instance.yaml \
  scripts/compose-with-secrets.sh -f docker-compose.yml -f docker-compose.mission.yml up
```

The override uses a Compose config, so a missing file fails loudly instead of becoming a directory.
On every Compose `up`, the one-shot `migrate` service applies schema files before runtime services
start, so an existing named volume cannot silently lag behind rebuilt code. Restarting `app` alone
does not run migrations. Migrations never seed business content.

## Useful checks

```sh
scripts/compose-with-secrets.sh ps
scripts/compose-with-secrets.sh exec postgres psql -U aq_owner -d aq -c   "select extname from pg_extension where extname in ('age','vector') order by extname;"
scripts/compose-with-secrets.sh exec postgres psql -U aq_owner -d aq -c   "select count(*) from ralph_frame_dimensions; select count(*) from causal_principle;"
scripts/compose-with-secrets.sh exec app codex login status
curl -fsS http://localhost:8090/health
curl -fsS http://localhost:8090/api/flagship
# Token for the rare >$3/high-blast-radius approve/reject action:
scripts/compose-with-secrets.sh exec app cat /var/run/aq/approval_token
```

Back up the durable database with:

```sh
scripts/compose-with-secrets.sh exec -T postgres pg_dump -U aq_owner aq > backup.sql
```

App rebuilds and restarts do not restart or erase PostgreSQL. Remove the database only when you
intend a destructive clean start: `scripts/compose-with-secrets.sh down -v`.
