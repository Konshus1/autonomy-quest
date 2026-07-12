# The single container

**Goal: the entire system in one Linux container. One `docker run`, and you have it.**

This is what makes the thing handable. Not "install Postgres, then install AGE, then set up a
graph database, then configure a runner, then..." — one image, one command, everything inside.

```sh
docker run -d --name autonomy-quest \
  --env-file .env \
  -p 8080:8080 \
  -v aq-data:/var/lib/postgresql/data \
  autonomy-quest:latest
```

## What's inside

| Component | Why it's here |
|---|---|
| **Postgres 16 + Apache AGE** | Records *and* relationships in one database. This is the choice that lets the whole system be one container — a separate graph DB would force a second service and break the story. |
| **Loop runner** | The engine. Observes, decides, acts, records, learns. |
| **Model gateway** | Talks to OpenRouter (or whichever provider the interview chose). Enforces the money budget. |
| **Web UI** | `:8080`. Watch the loop, steer it, change the mission. |

Supervised by a small init so one container can hold several processes without any of them dying
silently. If the loop runner crashes, it comes back and the crash is *recorded* — a component that
dies quietly is a component that lies about being alive.

## State

Everything durable lives in the Postgres volume: the mission, the work, the history, the learnings.
Back that volume up and you have backed up the instance. Nothing important lives on the container
filesystem.

```sh
docker exec autonomy-quest pg_dump -U aq autonomy_quest > backup.sql
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
docker build -t autonomy-quest:latest .
```

Or let the coding agent do it: `install.sh` builds and runs this for you after the interview,
using the answers in `instance.yaml`.
