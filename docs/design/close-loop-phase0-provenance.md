# Close-loop Phase 0: behavioral provenance and limits

**Status:** executable local proof only. Public actuation is unavailable.

Phase 0 demonstrates the smallest real Git path from a worker commit through an
independent review receipt and a deterministic gate to an AQ-local integration
ref. It is not a production merge train.

## Behavioral provenance

The behavioral reference is upstream TalkingBack `origin/main` at
`cd3a76d61c937ab8a78250bee9bbb47ab477bb25` (abbreviated `cd3a76d61`). That
revision is provenance, not a dependency or an authority imported at runtime.
In particular, this slice retains two useful TalkingBack/Ralph properties:

- a worker's completion claim is not its own review verdict; a separately named
  reviewer recomputes the required commands; and
- review is materialized in a disposable, detached Git worktree at the exact
  candidate object, rather than whatever a mutable task branch happens to name.

The upstream implementation also rejects vacuous pytest success. Phase 0 keeps
that behavior at the gate: a required pytest observation must report a positive
collection/pass count, and any skipped tests cause refusal. A moved candidate
ref cannot reuse a receipt for another SHA.

This is a behavioral port, not a line-for-line copy. TalkingBack's task tables,
session lifecycle, tmux workers, readiness state, absolute worktree layout, and
remote task-branch conventions are not present here. The commit pin makes the
comparison auditable; it does not imply that the upstream project endorses or
secures this implementation.

## Implemented authority path

1. `runner/close_loop/runtime.py` creates a real worker branch and commit. The
   commit author/committer is derived from an `AuthenticatedIdentity`, and the
   resulting base and candidate are full Git object IDs.
2. A different authenticated principal runs an argv-only `TestCommand`
   manifest in a temporary detached worktree. The receipt records the exact SHA,
   identities, manifest, process results, and pytest counts. The worktree is
   removed before the receipt returns.
3. `runner/close_loop/gate.py` fails closed over commit existence, non-empty
   change, source-worktree cleanliness, worker/reviewer separation, receipt
   identity and SHA binding, exact manifest equality, protected paths, required
   observations, pytest counts, integration conflicts, a supplied target
   observation, and optional remote read-back evidence. It constructs the
   integration tree with Git without updating a ref.
4. `runner/close_loop/actuator.py` supports `off`, `shadow`, and `local`. Local
   mode creates a no-ff-shaped commit object from the gate-bound integration
   tree and then compare-and-creates only
   `refs/aq/integration/<attempt>`. It does not update `main`, task branches,
   tags, remote-tracking refs, task state, or a remote.

The tests use temporary real repositories and a bare remote. They prove a
worker-authored task branch, distinct reviewer identity, exact detached/clean
review worktree, same-principal and SHA/ref mismatch refusals, a positive gate,
a negative gate matrix, integration conflict handling, local-ref-only
actuation, and public refusal with no remote mutation.

## Deliberate limitations

These boundaries are important and should not be inferred away:

- `AuthenticatedIdentity.authentication` is presently a non-empty opaque value.
  There is no cryptographic identity, credential validation, signature, or
  durable principal registry.
- The worker API applies a supplied mapping of file changes in the caller-owned
  checkout. It is not a sandboxed agent runner, and it switches that checkout to
  the candidate branch.
- The review manifest is supplied by the caller. It is not signed, loaded from a
  protected policy store, or persisted in an append-only evidence ledger.
- The gate validates recorded review observations but does **not** rerun the
  manifest against the integration tree. `observed_target_sha` and remote
  read-back fields are evidence supplied by an adapter, not network reads made
  by the gate. There is no decision signature, digest, nonce, expiry, lease, or
  durable replay protection.
- The actuator trusts an approved in-process `GateDecision`; it does not
  re-resolve the target branch or authenticate a serialized decision. Its
  compare-and-create protects only the new AQ ref name. Git object creation may
  precede a refused duplicate ref update, leaving an unreachable local object.
- There is no shared queue, scheduler, SQL attempt ledger, crash recovery,
  cleanup service, source-task freshness adapter, task closer, or public merge
  implementation.

Public mode always raises `public_interlock_pending`. Its three named prerequisites
are a fresh passing refusal proof for the exact gate version and platform,
approval from `c2f-4834-analogies-research`, and approval from Kevin. These are
explanatory constants, not flags or caller claims that can enable a push. Adding
an authenticated approval/proof ledger, remote fetch/push/read-back, and public
mutation requires a separate reviewed phase; this build contains no `git push`
actuation path.
