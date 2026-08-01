# Adversarial code review — Task #4407 Ralph→Autonomy-Quest dual-kit port

You are an **adversarial reviewer** (codex). Your job is to try to BREAK this work, not to
praise it. Assume the author is competent but wrong somewhere. Find the somewhere.

## Where the code is

- Review worktree (your cwd): `/Users/kevincthomas/src/aq-4407-review` (detached at the tip commit).
- Branch under review: `task/3981-causal-port-checkpoints`. The #4407 commits are `b00b66a..HEAD`
  (`git log --oneline b00b66a..HEAD`).
- A live OOTB container is running for you to attack:
  - Management API + React shell: `http://localhost:18195` (`/`, `/health`, `/api/ralph/state`,
    `/api/tasks`, `/api/agent-comms`, `POST /api/replication/propose`, `POST /api/manager/merge-decision`).
  - Its Postgres: `postgresql://aq:x@localhost:55495/aq`.

## Locked doctrine the code MUST honor (violations = findings)

1. Merge is **manager-gated**: `approve_merge` | `escalate_human` — never a silent auto-merge.
2. Replication: guest **proposes only**; **operator-gated by default**; host env
   `AQ_REPLICATION_AUTO_APPROVE=1|true|yes|on` is the ONLY override; the **host executes** the copy;
   the guest must **never** get docker.sock. No container spin-up in the executor.
3. Management API response **shapes are stable/additive**; `state.backing` may be `in_memory_stub`
   or `postgres` but nothing should branch on the literal.
4. A management-API crash must **never** stop the mission loop or gate container health.

## Attack surfaces — go hard at these

- `management/api/store.py`: the `PgStore` SQL. Are the `ralph_*` table names REALLY fixed, or can
  input reach them? JSONB payloads — any injection? Connection-per-op — leaks? The `build_store`
  fallback — does a mid-session DB drop truly degrade without 500s or data confusion? id synthesis
  (`comm-<seq>`, `task-<n>`) — collisions or races under concurrent writers?
- `ralph_portable/host_replication_executor.py`: does it REALLY prevent escaping `dest_root`?
  Try `instance_id`/mission values with `../`, absolute paths, symlinks, existing-dir clobber. Does
  `allocate_instance_id` sanitize enough? Can a request cause a write outside `dest_root`?
- `scripts/host_replication_broker.py`: can the operator gate be bypassed WITHOUT the override env?
  Does `--execute` ever act before approval?
- `container/{Dockerfile,entrypoint.sh}`: is `supervise_management` truly crash-isolated from the
  loop? Does a management crash affect health? Multi-stage build correctness; does anything leak
  `node_modules`/secrets into the image? Is the base-image `POSTGRES_PASSWORD` handling sane?
- `management/api/app.py`: validation gaps on POST bodies; does `/` ever 500 on a partial image?
- Tests: are `tests/test_*`, `management/e2e/*` (Playwright), and
  `tests/test_container_agent_interview.py` actually PROVING what they claim, or are they weak /
  tautological / mocking away the thing under test? Is the interview→mission-start Tier B a real
  proof or does the stub hide a broken loop? Does the Playwright suite really tie UI→DB?

## Run everything (don't trust green claims — reproduce)

```sh
cd /Users/kevincthomas/src/aq-4407-review
python -m pytest tests/test_ralph_control_config.py tests/test_manager_merge_gate.py \
  tests/test_replication_request.py tests/test_management_api.py \
  tests/test_ralph_portable_surprise_resolution.py tests/test_host_replication_executor.py \
  tests/test_management_store.py tests/test_management_comms_api.py -q
AQ_TEST_DB_URL=postgresql://aq:x@localhost:55495/aq python -m pytest tests/test_management_store.py -q -k pg_store
AQ_AGENT_DB_URL=postgresql://aq:x@localhost:55495/aq python -m pytest tests/test_container_agent_interview.py -q
cd management/e2e && npm install && npm run install-browser && AQ_MGMT_URL=http://localhost:18195 npm test
```

KNOWN pre-existing (NOT #4407 scope, do not count against this work unless you disagree with the
scoping): `tests/test_ui_states.py` (older stdlib-UI forced-state harness) needs a dedicated test DB
at `127.0.0.1:55433/autonomy_quest` and fails environmentally here. Say so if you think it should
block.

## Deliverable — a verdict, not a vibe

Produce a ranked findings list (most severe first). For EACH finding: `file:line`, a concrete
FAILURE SCENARIO (inputs → wrong outcome), severity (blocker/major/minor), and a suggested fix.
Then a single verdict line: **`VERDICT: APPROVE`** or **`VERDICT: REQUEST_REWORK`** (rework if any
blocker/major survives your own verification).

Report BOTH ways:
1. Post a BB note: use the `bb_create_note` tool (title `#4407 codex adversarial review`, tags
   `task_4407,adversarial_review,codex`).
2. Message the manager on the tbagents bus:
   `curl -s -X POST http://localhost:8000/api/agents/messages -H 'Content-Type: application/json' -H "X-Internal-Service-Token: $INTERNAL_SERVICE_TOKEN" -d '{"channel":"tbagents","sender_name":"<your-handle>","target_handle":"ccf-4407-ralph-to-aq","message_type":"response","payload":{"text":"VERDICT: ... <top findings>"}}'`

Do NOT modify the code. Review only. Be specific, be adversarial, be right.
