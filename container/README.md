# The single container

**Goal: the database substrate in one Linux container. One `docker run`, and Postgres + Apache AGE
plus the full autonomy-quest schema are ready.**

This is the path for people who do not want to install Postgres and AGE directly on their machine.
It brings up the durable substrate and then idles. A coding agent still has to run the interview,
write `instance.yaml`, add secrets to `.env`, and explicitly start `./aq.py once`, `./aq.py forever`,
or `./aq.py ui` when the instance has been aimed.

It does **not** auto-start the loop. An unaimed autonomous loop is not useful, and the public CLI has
no `aq.py loop` command to supervise.

```sh
docker build -f container/Dockerfile -t autonomy-quest:latest .

docker run -d --name autonomy-quest \
  -e POSTGRES_PASSWORD=x \
  -p 5432:5432 \
  -p 8080:8080 \
  -v aq-data:/var/lib/postgresql/data \
  autonomy-quest:latest
```

Open `http://localhost:8080` after it starts. With no `instance.yaml` mounted, the UI should say the
instance is **NOT ALIVE** and show that no mission is configured yet. That is honest: the substrate
is up, but the system has not been aimed.

The container generates `AQ_APPROVAL_TOKEN` at startup if you did not provide one, but it does not
print the token value to logs. It prints a retrieval command instead:
`docker exec <container> cat /var/run/aq/approval_token`. Keep the token with your local operator
notes; approving parked work in the UI requires it. To use your own, pass
`-e AQ_APPROVAL_TOKEN=...` or put it in the env file you provide to `docker run`; the entrypoint
writes the active token to that same `0600` file either way.

## What's inside

| Component | Why it's here |
|---|---|
| **Postgres 16 + Apache AGE** | Records *and* relationships in one database. This is the choice that lets the whole system be one container — a separate graph DB would force a second service and break the story. |
| **Full schema** | Every `schema/*.sql` file is applied on first volume initialization, including the AGE graph `autonomy_quest`. |
| **Status UI** | Runs on `:8080` even before the interview has produced `instance.yaml`, so the substrate has a visible bootstrap state. |
| **Public runner code** | `aq.py` and the runner code are in the image so an aimed instance can run explicitly with mounted config/secrets. |

The container is supervised by a small init and runs Postgres in the foreground. Docker health means
the substrate is queryable and initialized: the `aq` role/database exist, AGE is installed, the graph
exists, and the schema tables are present. The UI is supervised separately and restarts on crash
without exiting the container. When `instance.yaml` is present at startup, the aimed loop runs under
its own restart supervisor; without it, the loop stays idle.

## State

Everything durable lives in the Postgres volume: the mission, the work, the history, the learnings.
Back that volume up and you have backed up the instance. Nothing important lives on the container
filesystem.

```sh
docker exec autonomy-quest pg_dump -U aq aq > backup.sql
```

## Why one container and not compose

Compose is the right answer for a system a team operates. It is the wrong answer for a system a
stranger is trying for the first time on their laptop, because it turns "run this" into "understand
this first." Every additional service is another thing that can be half-up, another thing to explain
in the README, and another reason someone bounces off before the loop ever turns.

The tradeoff is real and we're taking it knowingly: one container is less operationally elegant and
much more likely to actually get run. Anyone who outgrows it can split it out — the components talk
over normal interfaces and nothing here is load-bearing on their colocation.

## Build

```sh
docker build -f container/Dockerfile -t autonomy-quest:latest .
```

Or let the coding agent do it: `install.sh` builds this after the interview when
`instance.yaml` selects container mode. It does not invent a mission or start the loop for you.
