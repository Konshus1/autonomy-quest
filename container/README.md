# Docker Compose: the complete local stack

A clean checkout starts the database and application with one command:

```sh
scripts/compose-with-secrets.sh up
```

Then open **http://localhost:8090**. The status-only UI is at
**http://localhost:8080**.

The stack deliberately separates four services:

| service | contents | host ports |
|---|---|---|
| `postgres` | pinned prebuilt PostgreSQL 16 + Apache AGE + pgvector | none by default |
| `migrate` | one-shot schema owner; exits before runtime starts | none |
| `governance` | separately credentialed lifecycle writer/API | 8091 (loopback) |
| `app` | built React UI, FastAPI, status UI, Codex CLI, mission loop | 8090, 8080 |

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

## Container and agent sandbox

The app runs as the unprivileged `aq` user with all Linux capabilities dropped and
`no-new-privileges`. Codex tool commands still run in its `workspace-write` bubblewrap sandbox;
the Compose seccomp override enables the namespace syscall bubblewrap needs and does not disable
Codex approvals or its sandbox. `/app` and `/tmp` are the configured writable roots. Never replace
this with `--dangerously-bypass-approvals-and-sandbox`.

This is a single-user local stack, not a hostile multi-tenant boundary. Codex must read its
subscription credential, and the current Codex `workspace-write` policy restricts writes but allows
tool commands to read files visible to that Unix user, including `CODEX_HOME`. Do not run untrusted
missions or expose either UI beyond loopback.

ACT is nevertheless a separate authority principal. The executor builds its child environment from
an explicit allowlist and maps only the restricted `aq_actor` database credential to `AQ_DB_URL`.
It never inherits loop, migration, governance, approval, provider-key, or future unknown variables.
PostgreSQL denies `aq_actor` writes to `causal_edge` and every governance ledger. Even if ACT
inspects its same-user parent and finds the loop DSN, `aq_loop` also has no raw causal-edge or
lifecycle DML. Narrow owner-executed functions can only stage a completed-run-backed provisional
proposal or resolve an already-completed matching prediction. `aq_governance` exists only in the
separate governance container and alone may append lifecycle evidence, promotion, or demotion
transitions. The schema-owner credential exists only in the one-shot migration container. Random passwords and governance tokens are
created outside the checkout by `scripts/compose-with-secrets.sh` and are not baked into Compose.
This protects the causal authority boundary; it does not turn the shared local container into a
multi-tenant secret-isolation system.

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
On every app boot, the idempotent migration pass reapplies schema files so an existing named volume
cannot silently lag behind rebuilt code. It never seeds business content.

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
scripts/compose-with-secrets.sh exec -T postgres pg_dump -U aq aq > backup.sql
```

App rebuilds and restarts do not restart or erase PostgreSQL. Remove the database only when you
intend a destructive clean start: `scripts/compose-with-secrets.sh down -v`.
