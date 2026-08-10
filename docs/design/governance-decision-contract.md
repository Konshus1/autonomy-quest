# Pre-ACT governance decision contract

This contract applies only when `AQ_GOVERNED_FEEDBACK` is explicitly enabled. The release default
remains off. The loop calls one narrow authenticated URL from `AQ_GOVERNANCE_URL`; it never derives
that URL from the broad management API or localhost.

## Request

The request is an immutable decision candidate, sent after DECIDE, intent/conflict/acquisition and
human gates, but before expense reservation, run creation, acquisition start, or ACT:

- `global_plan_id`: `<urn:uuid instance>/plan/<uuid>`, stable across retries;
- `work_id`: the already-durable decision row;
- the complete structured plan, including every step ID, action, expected effect, expected
  direction, and canonical scope.

The server authenticates the caller, verifies the instance namespace, digests the complete request,
and persists exactly one replay-safe authorization receipt. A replay with different content is an
error.

## Dispositions

- `allow`: every step has one exact, current promoted governor and every stored relation direction
  matches the requested direction. `may_act=true`, `selected=true`, `governed=true`.
- `block`: at least one exact current promoted governor contradicts a requested direction.
  `may_act=false`; the work is rejected before any run/ACT. Both booleans are false.
- `abstain`: the service is healthy but at least one step has no exact current promoted governor,
  and none contradicts. `may_act=true` under the existing autonomy/human gates, but both governance
  booleans are false. Abstention is not permission or authority.
- `defer`: the narrow service is unreachable, unauthenticated, malformed, or cannot durably record
  the decision. `may_act=false`; work is parked before any run/ACT. A loop-owned outage receipt
  records the exact global plan and reason without claiming authority.

Only `allow` can create governed-use rows. All four cases persist an exact reason, request digest,
global plan identity, selected/governed booleans, and any derived governor transition IDs. HTTP
success without a durable receipt is invalid. Unknown dispositions, missing fields, digest/global-ID
mismatches, and timeout/HTTP/JSON failures map to `defer`, never to `allow` or `abstain`.
