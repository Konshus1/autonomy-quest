# Docker Compose: the complete local stack

A clean checkout starts the database and application with one command:

```sh
docker compose up
```

Then open **http://localhost:8090**. The status-only UI is at
**http://localhost:8080**.

The stack deliberately has two services:

| service | contents | host ports |
|---|---|---|
| `postgres` | pinned prebuilt PostgreSQL 16 + Apache AGE + pgvector | none by default |
| `app` | built React UI, FastAPI, status UI, Codex CLI, mission loop | 8090, 8080 |

`EXPOSE` does not publish a port; the host mappings live in `docker-compose.yml`. PostgreSQL
port 5432 stays on the Compose network. For local database development only, use:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
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
docker compose -f docker-compose.yml -f docker-compose.dev-auth.yml up
```

That override mounts `${HOME}/.codex/auth.json` read-only. Never publish an image or Compose
bundle containing that file.

## Fresh database contents

On a new `aq-postgres-data` volume, official Postgres initialization applies:

- `schema/001_init.sql` through `schema/009_causal_edges.sql`;
- `CREATE EXTENSION age` and `CREATE EXTENSION vector`;
- the AGE graph `autonomy_quest`;
- the 21 canonical active frame dimensions;
- an empty causal-principle corpus;
- the versioned `workflows/default/v1/workflow.yaml` (`deterministic: true`) in the app image.

No mission, task, episode, learning, or fleet-specific causal principle is seeded. An unaimed
stack serves both UIs but does not start autonomous work. After the interview, aim it explicitly:

```sh
AQ_INSTANCE_FILE=/absolute/path/to/instance.yaml \
  docker compose -f docker-compose.yml -f docker-compose.mission.yml up
```

The override uses a Compose config, so a missing file fails loudly instead of becoming a directory.
The entrypoint validates mission objective and measure on every boot; a `{}` placeholder never
becomes an aimed loop merely because the container restarted.

## Useful checks

```sh
docker compose ps
docker compose exec postgres psql -U aq -d aq -c   "select extname from pg_extension where extname in ('age','vector') order by extname;"
docker compose exec postgres psql -U aq -d aq -c   "select count(*) from ralph_frame_dimensions; select count(*) from causal_principle;"
docker compose exec app codex login status
curl -fsS http://localhost:8090/health
```

Back up the durable database with:

```sh
docker compose exec -T postgres pg_dump -U aq aq > backup.sql
```

App rebuilds and restarts do not restart or erase PostgreSQL. Remove the database only when you
intend a destructive clean start: `docker compose down -v`.
