# Autonomy Program: mission to verified merge

**Status:** design for review; no merge actuator is enabled  
**Owner:** Track B (generalized autonomy, independent of analogy)  
**Review gate:** `c2f-4834-analogies-research` and Kevin must approve the refusal proof before public auto-merge is enabled  
**Date:** 2026-08-10

## 1. Outcome and boundary

The target is one closed, auditable loop:

```text
mission + shared task queue
  -> choose and claim one useful task
  -> materialize bounded work
  -> worker changes a branch
  -> independent reviewer verifies the change
  -> deterministic merge gate accepts or refuses it
  -> land it on main
  -> read main back from git
  -> complete the source task and learn from the outcome
```

This program does not depend on analogical reasoning. Analogies may later contribute candidates or
ranking features, but the queue, verifier, gate, and merge actuator must work when that contribution
is absent.

The first release closes the queue and merge gaps. Mission ranking and decomposition are deliberately
second. The simplest trustworthy loop is better than a broad planner with no reliable way to land.

## 2. Ground truth at the design point

### AQ

AQ's mission loop and the shared task queue are separate today.

* `runner/loop.py` creates `work` from an LLM decision over `instance.yaml`; it does not consume the
  shared `task` table. `Db.pending_autonomous_work()` selects old `work` rows, not queued tasks.
* `work` and `runs` have no source-task, branch, commit, worker, reviewer, or merge-receipt identity.
* `runner/merge_sync.py` builds a manager-decision packet after evaluation. The management endpoint
  validates and stores that packet, but neither component reads git or performs a merge.
* On current `main` (`f8cb369` when this design was written), the optional Ralph-control surfaces are
  still a partial kit. The management `ralph_tasks` queue only creates/lists `open` items, the
  in-process evaluator classifies actor-supplied evidence, and an `approve_merge` record is not a git
  operation. Work from this session may exist on task branches, but the release gate must not assume
  it is on main until git proves it.

That last distinction is intentional. This design uses the brief's verified worker/reviewer middle as
an input contract, while making its presence on the actual target branch a Phase 0 prerequisite. It
will not turn a design assumption into merge authority.

### What is load-bearing in Ralph

Ralph's useful pattern is not its size. It is the separation of durable states and the repeated
read-back of the real substrate:

* the shared `task` row is source intent;
* `bb_task_ralph_readiness` is Ralph-owned orchestration state;
* dispatch requires both `ready_for_worker_launch=true` and a `launch_ready_*` status, rather than a
  stale boolean alone;
* broad or under-specified tasks are researched/decomposed before dispatch;
* worker, task, runtime, evaluator, closeout, and human-review states are distinct;
* a completion packet does not complete a task until durable evidence is checked.

The real partial merge actuator is concrete. The enabled scheduler runs
`scripts/ralph/auto_closeout_sweep.py --apply` with an exact merge-authority grant. For conforming
recent solo/small-cohort packets it pins candidate branch SHAs, reads remote main with `ls-remote`,
fetches and rechecks it, assembles a temporary cohort integration tree, runs scoped pytest, rejects
branch/base drift, builds real `--no-ff` merge commits in another detached worktree, proves the landing
tree equals the tested tree, rechecks remote main, performs an atomic normal push, reads the remote SHA
and branch containment back, and only then closes tasks
(`app/services/ralph_cohort_auto_merge.py` in the live scheduler checkout).

This is narrower than the premise of full Ralph autonomy. `c2f-1557-manager` confirmed that general
cohort-to-integration and integration-to-main stages are not fully built; today that manager still
lands most verified cohorts manually in throwaway worktrees. The headless sweep handles only packets
with its exact top-level shape, pushed `origin/task/<id>-*` ref, 40-character pinned SHA, scoped pytest
targets, intent adequacy, and current-main descent. Its latest ticks had no eligible candidates.

Ralph's failures are equally useful. The live launcher is a divergent scheduler checkout rather than
the nominal repo. At design time its authoritative queue refill was blocked, bad persisted
`repo_cwd` values caused the same fail-closed dispatch refusals every tick, and a second enabled loop
passed `max_active_workers=18` to a schema capped at 16 and exited repeatedly. Its cohort gate also
records dirtiness as `skipped` when no source worktree exists, and pytest exit zero does not by itself
prove that every required test was collected rather than skipped. Historical failures include stale
readiness mirrors, completion flags without commits, SHA-ancestry false negatives after cherry-picks,
cached refs stated as current truth, and truncated/undelivered prompts. AQ should copy the successful
read-back and race invariants, fix the refusal holes, and not copy Ralph's endpoint count or split-tree
operations.

## 3. The smallest architecture

Add one bounded supervisor tick and two AQ-owned ledgers. Do not replace the existing mission loop.

```text
QueueBridge -> existing worker/reviewer path -> MergeGate -> MergeActuator
      ^                    |                       |              |
      +-------- task_work_link --------------------+------ merge_attempt
```

Each tick performs at most one state-changing action. It is safe to invoke repeatedly from the
existing scheduler:

1. reconcile an interrupted claim or merge attempt;
2. otherwise import/claim one eligible task;
3. otherwise advance one claimed work item through worker/reviewer processing;
4. otherwise evaluate one merge-ready item;
5. otherwise land one already-gated item if the configured merge mode permits it.

The supervisor is deterministic around state transitions. Models may rank candidates and propose a
bounded contract, but they cannot write merge acceptance or run git mutations.

## 4. Queue bridge: `task` to `work`

### AQ-owned link, shared source

Do not overload `task.status` with AQ's internal states. Add a versioned `task_work_link` table:

| field | purpose |
|---|---|
| `id` | AQ identity |
| `source_system`, `source_task_id` | stable queue identity; unique together |
| `source_version_hash` | hash of title, description, details, parent and modified time that was ranked |
| `mission_hash` | exact mission/config version used to admit it |
| `work_id` | unique link to `work` once materialized |
| `state` | `eligible`, `claimed`, `materialized`, `reviewing`, `merge_ready`, `merged`, `refused`, `blocked` |
| `lease_owner`, `lease_until`, `attempt` | crash-safe claim |
| `rank_packet`, `contract_packet` | immutable inputs/decision evidence |
| timestamps and `last_error` | recovery and audit |

Add `task_work_link_id`, `parent_work_id`, and an `execution_path` discriminator to `work`. Existing
mission rows use `execution_path=mission`; imported code tasks use `worker_reviewer`. Both
`approved_work()` and `pending_autonomous_work()` must explicitly select only mission rows. This is a
safety boundary: current `runner/` has no repo worker/reviewer runtime, so an imported coding task must
not fall through to the resident mission ACT executor.

Do not make stock AQ installation depend on TalkingBack's table existing: a `QueueSource` adapter reads
it when configured. In the co-located installation, the link and work rows are created in the same
PostgreSQL transaction with a deferred paired foreign key so their redundant ids cannot disagree. For
a future remote queue it uses the same source identity and idempotency key, not a pretend cross-
database transaction.

### Claim protocol

For a co-located database:

1. begin a transaction;
2. select a bounded set of eligible, nonterminal tasks and lock candidates with
   `FOR UPDATE SKIP LOCKED`;
3. recompute eligibility from the source row and current mission;
4. insert or compare-and-swap `task_work_link` on `(source_system, source_task_id)`;
5. create exactly one `work` row and bind its id to the link;
6. commit, then read both rows back.

A crash before commit creates neither. A crash after commit is recovered from the link. An expired
lease can be reclaimed only if git/session/readiness evidence shows no live owner. A duplicate tick
returns the existing link and work; it never creates a second branch.

Initially, AQ's candidate universe is intentionally narrow: only tasks with an explicit
`details.aq_phase1.admit=true` envelope. The bridge locks one such source row first, then locks and
reads its readiness row in the same transaction; a missing or stale readiness row becomes a durable
`blocked` result. It does not scan or refuse the heterogeneous shared queue. An admitted coding task
must name a configured repo, bounded definition of done, fixed verifier manifest, no unresolved
dependency, and authority within the instance boundary. Unrelated queue rows remain untouched; every
explicitly admitted row receives a durable positive or negative result.

## 5. Work, review, and merge-ready contract

Before dispatch, the materialized work contract must contain:

* source task id and immutable source hash;
* repo id and canonical repo path (from an allowlist, never free-form worker input);
* target ref (`refs/heads/main` for this program);
* bounded scope, definition of done, and stop condition;
* repo-owned required test manifest and expected named checks;
* worker identity and branch name;
* reviewer identity distinct from the worker;
* expected evidence locations.

A work item becomes `merge_ready` only when filesystem and git inspection proves all of the
following:

* the candidate ref resolves to a commit and differs from the pinned base;
* the candidate worktree is clean, with no untracked evidence/code being mistaken for committed work;
* the expected changed paths are present in `git diff <base>...<head>` and protected paths are absent;
* the fail-first receipt names the defect, command, negative result, fixed result, and exact candidate
  SHA; its digest matches the stored artifact;
* an independent reviewer ran the repo-owned checks against that SHA and recorded pass/fail per
  expected check;
* any required check that was absent or skipped is a refusal, not a pass.

The worker can propose these facts. Only the reviewer/merge-gate read-back can establish them.

## 6. Deterministic merge gate

The merge gate is a pure decision over a pinned candidate plus observed evidence. It produces a
signed/digested `MergeDecision`; it does not merge. The actuator accepts only an unexpired decision
whose gate version, base SHA, candidate SHA, test-manifest hash, and evidence digest still match.

### Gate checks, in order

1. **Policy:** merge mode, repo, remote, target ref, change-size/risk band, and protected paths are
   allowlisted. Default mode is `shadow`; public main is disabled.
2. **Identity and separation:** source task, link, work, run, branch, worker, reviewer, and gate
   identities exist; worker != reviewer; no identity is supplied only as prose.
3. **Source integrity:** re-read the task and reject if its version hash changed after review.
4. **Remote base:** `git fetch --prune` and resolve the target from the remote. Reject if it differs
   from the reviewed base. Never merge against a cached local `main`.
5. **Candidate reality:** resolve and object-check the candidate SHA; require a nonempty committed
   diff, clean candidate worktree, expected content, and no forbidden path.
6. **Integration construction:** create a fresh temporary worktree detached at the pinned remote
   base. Apply the candidate with the declared strategy (`--no-ff` merge for the first release).
   Any conflict or unexpected commit set refuses the attempt.
7. **Verification content:** run fixed commands from a repo-owned manifest inside that integration
   tree. Parse machine-readable results. Every required test id must be present and passed; nonzero,
   timeout, zero collected tests, malformed output, missing id, or skip refuses the attempt.
8. **Evidence binding:** compare results and changed content with the definition of done and independent
   review packet. “Tests ran” or “artifact exists” is insufficient.
9. **Freshness:** immediately before authorizing, re-fetch and re-check the remote base, candidate,
   policy hash, and source task. A race returns `stale_base`, never “close enough.”

A decision is one of `approve_local`, `approve_public`, `refuse`, or `escalate_human`, with stable
reason codes and all command results. Uncertainty cannot map to approval.

### `merge_attempt` ledger

Persist append-only attempts with: source/link/work/run ids; repo/remote/target; base, candidate,
integration and observed-remote SHAs; identities; gate/policy/test-manifest versions; command argv,
exit code, timeout, output digest and parsed required-test results; decision/reason codes; actuator
mode; and timestamps. Large stdout stays in a content-addressed artifact; the row holds its digest.

The current free-form `ralph_merges` packet may remain a UI projection, but it is not authority.

## 7. Refusal proof before trust

The gate is not trusted because a good branch passes. Its release artifact must show the same gate
binary/version refusing each controlled defect:

| injected defect | required refusal |
|---|---|
| task says done but branch has no commit | `candidate_empty` |
| expected file is untracked only | `candidate_dirty_or_untracked` |
| reviewer and worker are the same identity | `role_separation_failed` |
| receipt exists but names another SHA | `evidence_sha_mismatch` |
| required test fails | `required_test_failed` |
| required test is skipped or not collected | `required_test_missing` / `required_test_skipped` |
| worker substitutes an easier test command | `test_manifest_mismatch` |
| candidate changes the gate, manifest, workflow, secrets, or deployment path | `protected_path` |
| merge-tree conflict | `integration_conflict` |
| remote main moves during verification | `stale_base` |
| push is rejected or remote read-back differs | attempt is not `merged`; `remote_not_verified` |

Tests must also assert no target ref, remote ref, task terminal status, or cleanup state changed on every
refusal. A skipped refusal test fails the release harness. The refusal suite records the expected
negative result first and one positive control second.

Public mode has an additional boot interlock: a fresh, passing refusal-proof record for the exact gate
version and platform, plus an explicit approval record from `c2f-4834-analogies-research` and Kevin.
Without all three, `AQ_AUTO_MERGE_MODE=public` fails closed at startup.

## 8. Merge actuator and reversibility

Modes are monotonic in authority:

* `off`: inspect nothing automatically;
* `shadow` (default): run the full gate and record `would_approve`/refusal; no git ref mutation;
* `local`: create `refs/aq/integration/<attempt>` only; never change a remote;
* `public`: update the allowlisted remote main only after the interlock above.

For public landing, the actuator holds a per-repo/target advisory lock, rebuilds the integration commit
from the pinned base, reruns the gate, then performs a compare-and-swap push using the reviewed base as
the lease. After push it fetches and checks `ls-remote`/`rev-parse` equality and verifies that the
candidate tree content is present. Only that read-back permits `merge_attempt=merged`, source
`task_work_link=merged`, and source task completion. Cleanup happens last.

No force push or reset is permitted. The integration commit preserves a parent at the previous main,
so a human can revert it. Automated public revert is a separate high-risk actuator and is out of the
first release; a post-merge smoke failure records an incident and stops further merges. This is more
honest than calling an unproved rollback “reversible.”

## 9. Mission-driven selection and decomposition (after closure)

FIFO is only a tie-breaker. After the bridge and shadow/local merge loop work, rank eligible tasks by a
versioned, stored packet:

```text
score = mission_fit
      + expected_measure_gain
      + learning/verification value
      + dependency unlock value
      - cost
      - uncertainty
      - blast radius
```

The scorer receives the exact mission, current measure, task contract, dependency state, recent failed
attempts, and capacity. It must return component scores, cited source fields, confidence, and a
falsifiable reason the top task beats the runner-up. Deterministic eligibility/risk gates remain
outside the model. Age prevents starvation only among similarly scored tasks.

If a task cannot be made dispatch-ready without guessing, the planner may create bounded child tasks
linked by `parent_task_id`. A child must independently state scope, definition of done, verifier,
authority, repo, stop condition, and how it advances the parent mission. Parent completion requires
all required children and a final parent verification; creating children is not progress by itself.

For the stated mission, early ranking should favor work that produces executable tests of mission-
oriented fleet management and validates causal hypotheses. Analogy and curiosity features receive no
special authority: they rank only through expected mission gain and evidence quality.

## 10. Recovery and concurrency invariants

* One source task maps to at most one live AQ work lineage.
* One candidate SHA has at most one live merge attempt per target base.
* Claims and merge authority expire; evidence does not silently become fresh again.
* On restart, reconcile `gate_running`/`push_running` from git and the remote before retrying. Never
  infer failure or success from an interrupted process alone.
* A pushed-but-unrecorded attempt is completed from remote read-back, not pushed twice.
* A recorded-but-not-pushed attempt is refused/retried only after proving the remote is unchanged.
* Task completion, worker retirement, and worktree deletion occur only after remote verification.
* Every state transition is compare-and-swap and emits an audit event.

## 11. Delivery sequence

### Phase 0: establish reality

1. Inventory the worker/reviewer implementation on the actual branch intended for release.
2. Add versioned schemas for `task_work_link` and `merge_attempt` plus state-machine tests.
3. Write the repo-owned merge test manifest and refusal fixtures.
4. Keep all merge modes at `off`/`shadow`.

Exit: a clean checkout can prove the shared task exists, show one unambiguous source-task-to-work
lineage, and run the refusal harness. No public ref changes.

### Phase 1: close the queue in shadow

Implement the co-located queue bridge, lease/idempotency recovery, narrow eligibility, and source
read-back. On the current branch, stop at durable shadow materialization: create one
`execution_path=worker_reviewer` work lineage, prove mission selectors cannot execute it, and perform
no branch/session side effect. Do not claim an “existing worker/reviewer path” until Phase 0 proves and
ports that implementation onto the release branch.

Exit: the task is not duplicated across restarts, a bad/ineligible task produces a durable negative
result and no work, and a good imported task remains durably non-executable by the mission ACT loop.

### Phase 2: prove merge refusal

Implement the pure gate, temporary integration worktree, fixed test manifest, ledger, and `local` mode.
Run every defect in Section 7 and independently review the proof.

Exit: all negative fixtures refuse without target mutation, the positive fixture creates only an AQ
local integration ref, and `c2f-4834-analogies-research` has reviewed the design/proof.

### Phase 3: gated public canary

Only after Kevin's approval, enable `public` for one repository, one target, one low-risk change class,
one merge at a time. Keep protected-path and size limits conservative. Verify remote SHA and task state
from a second process.

Exit: one canary lands and one deliberately bad candidate is refused in the same configured runtime.

### Phase 4: improve choice

Add mission scoring, bounded decomposition, calibration from outcomes, and broader change classes only
from observed false-positive/false-negative data.

## 12. Acceptance statement

AQ reaches Ralph-parity for this program when an unattended run can take one eligible shared task,
produce and independently verify a committed branch, refuse a deliberately defective candidate, land
a valid candidate under an approved public policy, verify the remote main SHA and content, and only
then close and clean up the task lineage. A status flag, decision packet, local merge, or green run
without the negative control does not satisfy this definition.
