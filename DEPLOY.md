# Deploying the autonomy-quest live loop

One command redeploys the live loop, and health is now *assertable* — "healthy" means a real DECIDE
cycle completed recently, not merely that a process is up.

This exists because the live loop once drifted 27h behind `main` and sat **down ~27h** (crash-looping
on missing Codex auth) with nobody noticing: the image carried no version, "health" meant process-up,
and the deploy was undocumented and reverse-engineered. #4834 fixes all three.

## One-command deploy

```sh
scripts/deploy.sh                 # deploy origin/main
scripts/deploy.sh <ref>           # deploy a specific ref / SHA / branch
```

`scripts/deploy.sh`:

1. **Fetches** and **checks out the target ref clean** in the build-context worktree
   (default `/Users/kevincthomas/src/aq-wt-mergetrain`).
2. **Rebuilds** compose project `aq-seed-c2f4834`, stamping the built commit into the image via the
   `GIT_SHA` build-arg (`build-arg → ENV AQ_GIT_SHA → /app/GIT_SHA → /health`).
3. **Waits for the one-shot `migrate` service** to finish successfully (fails loudly if it errors).
4. **Asserts `/health`** reports the deployed SHA **and a fresh heartbeat** (a real cycle within the
   threshold) before declaring success. If the loop is up but not cycling after the timeout, it fails
   loudly and points at the Codex-auth gotcha below.

It wraps this incantation (do not run it by hand — use the script, which also asserts liveness):

```sh
AQ_COMPOSE_SECRET_FILE=~/.local/state/autonomy-quest/seed-c2f4834/compose-secrets \
COMPOSE_PROJECT_NAME=aq-seed-c2f4834 \
GIT_SHA=$(git rev-parse HEAD) \
scripts/compose-with-secrets.sh \
  -f docker-compose.yml \
  -f ~/.local/state/autonomy-quest/seed-c2f4834/ports.yml \
  -f ~/.local/state/autonomy-quest/seed-c2f4834/selfcorr-override.yml \
  up -d --build
```

Key env overrides (live-stack defaults baked in): `AQ_DEPLOY_WORKTREE`, `COMPOSE_PROJECT_NAME`,
`AQ_SEED_STATE`, `AQ_DEPLOY_OVERLAYS`, `AQ_HEALTH_URL`, `AQ_DEPLOY_HEALTH_TIMEOUT`.

## Codex device-auth gotcha (the ~27h-down root cause)

The loop drives the Codex CLI and **crash-loops without a valid device login**. Auth lives in the
`aq-codex-auth` named volume and *survives* redeploys — but a first deploy, a wiped volume, or an
expired token means the loop will never cycle, and `deploy.sh` will fail at the heartbeat gate.

Log in **inside the app container** (interactive device flow; the token stays in the volume):

```sh
docker exec -it aq-seed-c2f4834-app-1 codex login --device-auth
```

then re-run `scripts/deploy.sh`.

## What's deployed, and is it cycling?

`/health` on the management API (`http://127.0.0.1:8090/health`) now answers both:

```jsonc
{
  "ok": true,                         // this endpoint answered (process liveness)
  "git_sha": "…",                     // the commit this image was built from
  "last_cycle_at": "2026-08-11T…Z",   // when a DECIDE cycle last completed
  "seconds_since_last_cycle": 42.0,   // age, computed in the DB (not the API clock)
  "cycle_count": 137,
  "cycling": true,                    // a real cycle within the freshness threshold
  "cycling_threshold_seconds": 900
}
```

- `git_sha` answers **what is deployed** (compare against `git rev-parse HEAD`).
- `cycling` answers **is the loop actually turning** — a crash-looping-but-up loop reads
  `cycling: false`, which is exactly the signal that used to go unnoticed.

### Liveness monitor

```sh
scripts/check_loop_alive.sh
# exit 0 = cycling | 2 = up but NOT cycling | 3 = /health unreachable
```

Poll it from cron/a monitor and alert on non-zero. Override the target with
`AQ_HEALTH_URL`. Tune the freshness window with `AQ_LOOP_CYCLING_THRESHOLD_S` (default **900s**),
read by the API at request time.

The default is 900s (≈2× the loop's 300s inter-cycle interval + margin), **not** 300s: the
heartbeat is written at cycle *end*, then the loop sleeps a full interval before the next cycle
starts and runs for T seconds before the next beat, so `seconds_since_last_cycle` peaks near
`interval + T` every period. A threshold at or below the interval reads a perfectly healthy loop as
`cycling:false` during every execution window. If you shorten the loop interval, keep the threshold
comfortably above it (≥ 2× interval).

**Hibernation caveat:** a *deliberately* hibernating loop (stopped, waiting for a human) also reads
not-cycling by design — that is a human-acknowledged stop, not a crash. Cross-check the `heartbeat`
table state (`turning` vs `hibernating`) or the `hibernation` record before paging on a known stop.

## How the heartbeat works (design)

- Written once per completed `cycle()` from a **fail-safe** hook (`Loop._record_cycle_heartbeat` →
  `Db.beat_cycle`), plus on a rate-limited wait (the loop turning-and-waiting). A heartbeat-write
  failure is swallowed — it can never crash the loop or change a decision. It is **not** written on
  the crash/agent-fail path, so a down loop honestly reads not-cycling.
- Stored in `loop_heartbeat` (`schema/028_loop_heartbeat.sql`): a single-row upsert of
  `last_cycle_at` + a monotonic `cycle_count`. Deliberately distinct from `loop_runtime` (a 2s
  *process* pulse) and the `heartbeat` state gate (turning/hibernating, which never counts as
  progress). It is a pure liveness signal — never authority or progress evidence.
- Grants: the migration explicitly grants `aq_loop` SELECT/INSERT/UPDATE (the upsert needs both
  INSERT and UPDATE). `aq_loop` also **retains DELETE** here via the blanket
  `GRANT ... ON ALL TABLES` in `schema/999` — this is intentional and harmless: the row is an
  idempotent single-row upsert, so a stray delete just makes `/health` read `cycling:false` until
  the next cycle re-inserts it. No revoke is added because this is not an authority/evidence table
  (unlike the append-only self-correction tables, which do lose UPDATE/DELETE).
