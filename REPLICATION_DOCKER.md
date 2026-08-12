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
ports. `straight_copy` = same image/code, no modifications (modifications are Step 2).

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

```sh
AQ_REPLICATION_DOCKER_TEST=1 AQ_REPLICATION_MIN_FREE_MEM_PCT=10 \
  python3 -m pytest tests/test_replica_stack_docker.py tests/test_replication_daemon_docker.py -q -s
```
