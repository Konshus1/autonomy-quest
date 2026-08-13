# Self-replication Step 1 — real Docker container copy (`straight_copy`), host-executed

> **⚠️ POWERFUL AND RISKY.** With the testing override on, a guest container's proposal causes the
> **host** to stand up a *real, independent* Docker Compose stack — a full ~4-service replica. Left
> ungated this is a fork-bomb / resource-exhaustion vector. Every safety rail in this document is
> load-bearing. The public repo default is **operator-gated and disabled**; the container spin-up only
> happens when a human enables the override in *our* seed environment. Do not commit the override on.

## The flow

```
guest container                          HOST (never shares docker.sock with the guest)
──────────────                           ────────────────────────────────────────────────
POST /api/replication/propose  ──────▶   scripts/host_replication_broker.py
  {mode: straight_copy, ...}               1. validate + approve
  (writes a proposal row only)             2. (optional) safe filesystem instance-dir copy
                                              ralph_portable/host_replication_executor.py
                                           3. stand up a NEW isolated stack — HOST ONLY
                                              ralph_portable/host_replica_stack.py
                                                • gate: cap (bounded)
                                                • gate: free memory (resource-gated)
                                                • allocate aq-replica-<id> + AQ_INSTANCE_ID
                                                • probe 4 free host ports
                                                • generate secret file + !override ports overlay
                                                • compose-with-secrets.sh -f docker-compose.yml
                                                    -f <ports overlay> up -d --build
                                                • ASSERT /health: reachable + git_sha present
                                                • record replica.json (enumerable, reap-able)
                                           4. teardown when done
                                              scripts/host_replication_teardown.py
```

The guest **only writes a proposal row**. It never executes docker. The host broker is the sole caller
of the docker step.

## Hands-off flow — the auto-executor daemon (Step 1.5, MOST POWERFUL)

> **⚠️ THE MOST POWERFUL SURFACE IN THE REPO — TESTING ONLY, NEVER THE LIVE LOOP BY DEFAULT.**
> The broker above still needs a **human** to run it. The daemon removes the human: a guest writes an
> approved/auto-approved `straight_copy` proposal and — with **zero further action** — the host stands
> up a real replica. Enable it **only** in a scoped, isolated test environment. Do **not** point it at
> the live `aq-seed-c2f4834` proposals, and never commit its opt-in envs on.

```
guest container                     HOST daemon (host operator starts it; NOT in the compose stack)
──────────────                      ──────────────────────────────────────────────────────────────
POST /api/replication/propose ───▶  scripts/host_replication_daemon.py --once | --interval N
  {mode: straight_copy, ...}          poll: approved/auto_approved, not-yet-executed straight_copy
  (status=auto_approved via           for each -> stand_up_replica_stack (SAME cap+memory gates,
   the AUTO_APPROVE override)                        SAME host cap flock) -> aq-replica-<id>
                                      record executed proposal in a host ledger (idempotent)
```

`scripts/host_replication_daemon.py`:

```sh
# Double opt-in REQUIRED (both, or it refuses and exits 3 before touching docker):
export AQ_REPLICATION_DAEMON_ENABLE=1      # explicit host opt-in for the auto-executor
export AQ_REPLICATION_AUTO_APPROVE=1       # the testing override (skips the operator wait)
export AQ_REPLICATION_MAX_REPLICAS=2       # keep the bound small
export AQ_REPLICATION_MIN_FREE_MEM_PCT=35  # keep the resource gate on

python3 scripts/host_replication_daemon.py --once          # drain pending, then exit (tests/cron)
python3 scripts/host_replication_daemon.py --interval 30    # poll every 30s until SIGINT/SIGTERM
```

Daemon safety properties (each has a test):

- **HOST-ONLY / outside the guest closure.** It imports the docker step, which the import firewall
  forbids any guest-reachable module from importing. `tests/test_import_firewall.py` proves the
  **full transitive** guest import closure contains neither the docker step nor the daemon.
- **Double opt-in, off by default.** Refuses unless **both** `AQ_REPLICATION_DAEMON_ENABLE=1` **and**
  `AQ_REPLICATION_AUTO_APPROVE=1` are set. Neither is committed on.
- **Bounded flood.** Every stand-up runs the cap + memory gates under the host cap flock, so a
  **flood** of proposals yields at most `cap` replicas, then refusals (the refused rows stay pending,
  not lost — they run later if capacity frees).
- **Idempotent.** A host-authoritative ledger (`daemon-ledger.json`, beside the replica state) records
  executed proposal ids, so a proposal is never stood up twice across `--once` runs or restarts. The
  daemon keeps its own ledger rather than trusting/mutating the guest-writable proposal store.

## Commands

Stand up (host, after approval/override):

```sh
# guest proposal already recorded; host executes:
echo '{"mode":"straight_copy","mission_id":"m1","requester_instance_id":"urn:uuid:..."}' \
  | AQ_REPLICATION_AUTO_APPROVE=1 python3 scripts/host_replication_broker.py --execute --stand-up-stack
```

Enumerate / tear down (the reversible rail):

```sh
python3 scripts/host_replication_teardown.py --list
python3 scripts/host_replication_teardown.py --project aq-replica-<id>
python3 scripts/host_replication_teardown.py --all      # every aq-replica-* stack
```

A replica is a **distinct compose project** `aq-replica-<shortid>` with its own project-namespaced
volumes (`aq-replica-<id>_aq-postgres-data`, `_aq-workspace`, `_aq-codex-auth`) and its own probed host
ports. `straight_copy` = same image/code, no modifications.

## Step 2 — bounded guest-requested modifications (`copy_with_modifications`)

A guest may PROPOSE that its replica be stood up with **modifications**. It never applies them itself;
the **host** applies only *validated, bounded* deltas into the replica stand-up. The policy lives in
`ralph_portable/replication_modifications.py` and is **deny-by-default**.

**CONFIG deltas — an ALLOWLIST of settable env-var keys.** A key passes **only** if it is in
`CONFIG_ALLOWLIST`:

| Allowlisted key | What it sets |
| --- | --- |
| `AQ_WORKFLOW_SELECTOR` | which workflow the replica runs (headline use case) |
| `AQ_MISSION_LABEL` / `AQ_MISSION_NOTE` | mission label / free-text note |
| `AQ_INSTANCE_LABEL` | display label for the replica |
| `AQ_MISSION_CADENCE` / `AQ_MISSION_MAX_ATTEMPTS` | bounded mission cadence / retry budget |

Everything else is **DENIED**. On top of deny-by-default an explicit denylist names the
host-security-relevant surfaces so the rejection reason is specific, and a self-consistency test proves
**no allowlisted key can ever collide with the denylist**:

- `AQ_REPLICATION_*` — a replica must **NOT** set its own cap / memory-gate / auto-approve / daemon /
  lock. This is the crux: allowing it would let a replica **disable its own replication safety gates**.
- Anything matching `DOCKER` / `SOCK` — no docker or socket access, ever.
- Anything matching `PASSWORD` / `SECRET` / `TOKEN` / `CREDENTIAL` / `DB_URL` / `DB_OWNER` — no
  credential or DB-owner theft.
- Anything matching `BIND` / `PORT` — no host-binding / published-port changes.
- Identity/governance/host keys (`AQ_INSTANCE_ID`, `AQ_GOVERNANCE_ADJUDICATOR`, `AQ_GOVERNED_FEEDBACK`,
  `AQ_LOOP_AUTORESTART`, `CODEX_HOME`, `AQ_STATE_DIR`, `COMPOSE_PROJECT_NAME`, `GIT_SHA`, …).

Config values must be flat scalars (str/int/float/bool, bounded length, no control chars) and are
rendered into a compose overlay via `yaml.safe_dump`, so a value can never inject compose/YAML structure.

**CODE deltas — DEFERRED to Step 3.** Step 2 is **config-deltas-only**; it does **not** ship a code-delta
apply path. The policy still *classifies* a requested code path today so the hard boundary is real and
tested: a delta touching safety/gate/host code (the replica stand-up, the replication broker/executor/
daemon, `replication_*`, the import firewall, `container/` / `docker-compose*` / `compose-with-secrets` /
`schema/999` / `.github/`, `..` traversal) is **REJECTED as a security violation**; a delta under the
designated safe prefixes (`workflows/`, `plugins/`) is recognized but **DEFERRED** (not applied). We
never ship an unbounded code-delta path.

**Validated at BOTH ends (defense in depth).**
- *Propose-time*: `POST /api/replication/propose` runs `validate_modification_packet` and returns **400**
  on any forbidden delta.
- *Host apply-time*: the docker stand-up (`stand_up_replica_stack`) and the filesystem copy executor each
  **re-validate the stored packet** and refuse (`gate="modification_policy"` / a failed record) before any
  side effect — the host never trusts what was proposed/stored. A tampered packet that bypassed propose is
  still refused. As a final belt-and-braces filter, only `allowlisted_config(...)` entries are ever
  written into the replica's generated config, and `render_config_overlay` re-filters to the allowlist.

**How it lands in the replica.** Validated config deltas are written to `<state>/config.yml`, a compose
overlay merged **after** the base file + ports overlay, injecting the allowlisted keys into the replica's
`app` service environment. The running replica reflects them at `GET /health/config`
(`{"allowlist": [...], "instance_config": {...}}`), which echoes **only** allowlisted keys — never a
secret, gate, or host key.

**THE LOAD-BEARING INVARIANT.** No modification — config or code — can (a) weaken the replica's own
replication safety gates (cap / memory / opt-in / host-executes), (b) grant the replica or its guest
`docker.sock`, or (c) set the replica to auto-replicate. **A modified replica is still fully sandboxed and
bounded exactly like a `straight_copy`** — the four invariants below hold unchanged. The auto-executor
daemon deliberately still executes only `straight_copy`; `copy_with_modifications` goes through the manual
host broker.

## The four non-negotiable safety invariants

1. **Host executes; the guest never gets `docker.sock`.** Every `docker`/`docker compose` call lives in
   `ralph_portable/host_replica_stack.py` + the host scripts, **never** in a guest-reachable module.
   `tests/test_import_firewall.py::test_guest_reachable_code_cannot_run_docker_replication` is the
   structural proof: the in-container management API (`management/api/app.py`) and the mission loop must
   not import the docker step, or CI fails.

2. **Bounded — hard replica cap.** `AQ_REPLICATION_MAX_REPLICAS` (default **2**). Before any spin-up the
   host counts **all** `aq-replica-*` compose projects (running *or* stopped) and refuses at/over the
   cap. The count is across generations and requester-agnostic, so a replica cannot fork-bomb.

3. **Resource-gated (mandatory).** `AQ_REPLICATION_MIN_FREE_MEM_PCT` (default **35**). The host checks
   free memory and refuses below the threshold. If memory can't be measured it **fails closed** (reports
   0% → refuse). A replica is a full stack; the host has rebooted from memory pressure before.

4. **Reversible.** Every replica is enumerable by project prefix and torn down with the `docker compose
   -p <proj> down -v` effect (containers + project volumes + networks) plus removal of its generated
   files. Teardown is label-based, so it reaps even a replica whose generated files are already gone —
   nothing orphaned.

5. **Cap holds under concurrency (TOCTOU flock).** The cap gate is a check-then-act: between "count
   `aq-replica-*`" and "compose up", a concurrent daemon poll or a manual broker could each observe
   `count < cap` and both spin up. The count→stand-up critical section runs under an exclusive
   host-side `flock` (a fixed lock path, so the daemon and the broker contend on the **same** lock),
   so the bound holds under concurrency. Released before the (slow) health poll — the new stack already
   counts in `docker ps -a` once `up -d` returns. Advisory lock, auto-released on process death (no
   stale lock); env-overridable via `AQ_REPLICATION_LOCK_FILE` for tests.

## `/health` liveness contract (what "a live independent replica" means)

The host asserts the replica by polling `http://127.0.0.1:<app_mgmt_port>/health` and requiring
`ok: true` **and** a present `git_sha` (the deploy-hygiene contract). It deliberately does **not**
require `cycling: true`: a `straight_copy` replica gets its **own empty codex-auth volume**, so its loop
hibernates awaiting device auth. Step 1 proves *"a new independent healthy stack stood up"*, not *"the
replica does work"*.

## Comms wiring — a replica comes up comms-READY (production stays inert)

A replica exists to communicate, so the stand-up brings it up **comms-ready with zero hand-editing**
(#4834 comms deploy wiring):

- **Tokens are plumbed.** `docker-compose.yml` (app service only — governance/evaluator do not host
  comms) references `AQ_COMMS_INSTANCE_TOKEN`, `AQ_COMMS_OUTBOX_READ_TOKEN`,
  `AQ_COMMS_PARENT_INSTANCE_ID`, and `AQ_COMMS_INBOX_TOKEN`, each defaulting to **empty**.
  `render_secret_file` generates the instance token **and** the outbox-read token into the replica's
  0600 secret file, so the host outbox relay can pull the replica's `/comms/outbox` with no manual
  token creation. (`scripts/compose-with-secrets.sh` mints the same two tokens for a standalone stack
  and upgrades an existing secret file to gain them without rotating established creds.)
- **A2A + emit ON by default (authority-inert).** `stand_up_replica_stack` sets `AQ_A2A_ENABLE=1` and
  `AQ_COMMS_EMIT=1` via the compose invocation env (the same mechanism that sets the replica's
  project/secret/ports) — no hand-edited overlay. The A2A façade is a proven-safe translation surface
  that grants **no** authority.
- **The importer command path stays OFF.** `AQ_COMMS_WORK_IMPORT` (the actuation path where an
  inbound work request becomes a queued task) is **never** enabled by the stand-up and is scrubbed
  from the replica's compose env even if set in the host environment. A child reports; it does not get
  the actuation path by default. It stays opt-in via an explicit operator overlay.
- **Production default is unchanged.** A base stack with none of these flags set has A2A **off**
  (`a2a_enabled()` False, `/a2a` 404), emit a no-op, and importer off — the live loop is
  byte-identical/inert. Only a replica (or an explicit overlay) turns comms on. Nothing here grants
  authority.

## Testing mode is SCOPED — not a committed default

The committed public posture stays safe:

- `AQ_REPLICATION_AUTO_APPROVE` is **unset** (operator approval required).
- No `replication.enabled=true` is committed.

To run the fast testing loop, set the override **in our seed stack's env/overlay only** (e.g. the
`seed-c2f4834` overlay), never in the repo:

```sh
AQ_REPLICATION_AUTO_APPROVE=1        # skip the operator wait (testing only)
AQ_REPLICATION_MAX_REPLICAS=2        # keep the bound small
AQ_REPLICATION_MIN_FREE_MEM_PCT=35   # keep the resource gate on
```

## Tests

- Unit (`tests/test_host_replica_stack.py`, no docker): cap gate refuses at N+1; memory gate refuses
  below threshold (and fails closed when unmeasurable); identity/project/ports are collision-free and
  traversal-safe; `!override` ports overlay; secret-file completeness; the gates fire **before** any
  spin-up; teardown reaps by label and removes files and refuses non-`aq-replica-*` projects; the
  **cap flock** is exclusive across fds and **two concurrent stand-ups at cap-1 → exactly one succeeds**.
- Daemon (`tests/test_replication_daemon.py`, no docker): double opt-in off by default (CLI exits 3);
  selects only approved/auto_approved `straight_copy`; idempotent across polls (ledger); a **flood of
  N>cap proposals → exactly `cap` executed, the rest refused at the cap gate**; the memory gate still
  fails closed via the daemon; **two concurrent polls cannot breach the cap**; the interval loop stops
  on request.
- Import firewall (`tests/test_import_firewall.py`): the guest-cannot-run-docker structural proof, now
  over the **full transitive** guest import closure (the docker step and the daemon are outside it; the
  daemon itself is on the host side of the wall).
- Integration (`tests/test_replica_stack_docker.py`, gated on `AQ_REPLICATION_DOCKER_TEST=1`): a full
  `straight_copy` — host stands up `aq-replica-<id>` → `/health` reachable + SHA → teardown removes it
  cleanly (0 containers, 0 volumes), asserting a **distinct** project and an **untouched** parent.
- Hands-off e2e (`tests/test_replication_daemon_docker.py`, gated on `AQ_REPLICATION_DOCKER_TEST=1`
  **and** host free memory >45%, else it skips loudly): seed an approved `straight_copy` into an
  isolated store → daemon `--once` stands it up with **zero manual broker** → `/health` reachable +
  distinct project + parent untouched → idempotent second poll → teardown → 0 `aq-replica-*` left.
- Modification policy (`tests/test_replication_modifications.py`, no docker): the allowlist is
  self-consistent; allowlisted config accepted and lands in the compose overlay; **every** forbidden
  delta (`AQ_REPLICATION_AUTO_APPROVE`, `AQ_REPLICATION_MAX_REPLICAS`, a DB secret/token, a
  ports/host-binding, docker/sock, identity/governance) is REJECTED at **propose** (the HTTP endpoint →
  400) *and* at **host apply** (`gate="modification_policy"`, compose never runs, no files); a **tampered
  stored packet** is refused host-side; a modified replica **cannot disable its own gates**; code deltas
  to safety/host paths REJECTED as security, safe-prefix deltas DEFERRED; `straight_copy` unchanged.
- Modification e2e (`tests/test_replica_modifications_docker.py`, gated on `AQ_REPLICATION_DOCKER_TEST=1`
  **and** psutil free memory >45%, else skips loudly): a `copy_with_modifications` with an allowlisted
  `AQ_WORKFLOW_SELECTOR` delta stands up a replica whose **`/health/config` reflects the delta** (and
  leaks no secret/gate key), still bounded (no docker.sock, distinct project), then teardown → 0 orphans,
  parent untouched. Never runs against the live loop.

```sh
AQ_REPLICATION_DOCKER_TEST=1 AQ_REPLICATION_MIN_FREE_MEM_PCT=10 \
  python3 -m pytest tests/test_replica_stack_docker.py tests/test_replication_daemon_docker.py \
    tests/test_replica_modifications_docker.py -q -s
```
