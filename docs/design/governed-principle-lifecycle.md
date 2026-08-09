# Governed principle lifecycle

AQ-owned mined rules now have a governance axis independent of their epistemic
`fuzzy/evidential/formal` axis: `mined → provisional → promoted → demoted`.

The append-only `causal_principle_transition` ledger is the source of state. There is no mutable
`is_active` flag that can disagree with status. Every row requires evidence, environment, actor,
rule version, and timestamp. Promotion additionally requires an independent adjudicator and the
full applies-here/negative-control packet. Demotion must be automatic. Updates and deletes are
rejected by a database trigger.

## Environment (load-bearing definition)

An environment is the canonical execution context `(domain, mission_id, harness)`. `harness`
includes the evaluator/config version. `environment_id` identifies a particular execution but is
not part of the context fingerprint, so aliases or retries of the same context do not manufacture
cross-environment evidence. Promotion requires supports from at least two distinct SHA-256 context
fingerprints and at least two distinct domains.

This is intentionally stricter than “two runs.” It prevents repeated success in one harness or a
renamed ID from being called transfer. It does not prove that caller-supplied domain semantics are
truthful; the separately credentialed authorization gate adjudicates the evidence. A future signed
environment registry can strengthen that remaining identity boundary without changing the ledger.

## Authority and shadowing

Provisional and demoted rules are excluded from ordinary Postgres-backed plan assessment. They can
be consulted only with `bounded_experiment=true`; each returned step is marked
`authoritative=false`. Only a promoted rule carries authority. "Bounded" describes the
experiment's declared scope and budget; it is not a one-shot counter.

Promotion is available through `/api/causal/governance/promote` only when
`AQ_GOVERNANCE_TOKEN` is configured and supplied in `x-aq-governance-token`.
`AQ_GOVERNANCE_ADJUDICATOR` binds that credential to the adjudicator identity instead of trusting
a request-body alias. Evidence ingestion uses a separate `AQ_GOVERNANCE_EVIDENCE_TOKEN`. The
adjudicator must differ from the recorded miner, two cross-domain supports must exist, and
applies-here plus an executed negative control are mandatory.

## Automatic demotion

`POST /api/causal/governance/test` classifies an observed measure delta against the expected
direction. A delta within `noise_tolerance` is `noise`, never refutation. A promoted rule is
automatically demoted only when a clearly opposite delta occurs in a domain and context absent
from the promotion evidence. Refutation in an already-tested domain is logged but does not claim a
cross-environment transfer failure. Demotion calls no human and is replay-idempotent by
`evidence_ref`; re-promotion remains possible through the expensive gate.

## Check

```bash
createdb -U "$USER" -h localhost aq_governance_test
AQ_GOV_TEST_DSN=postgresql://$USER@localhost/aq_governance_test \
  python -m pytest -q tests/test_principle_governance_pg.py
AQ_GOV_TEST_DSN=postgresql://$USER@localhost/aq_governance_test \
  python scripts/proof_governed_principle_lifecycle.py
```

The proof executes SQL actions and measures in PostgreSQL, writes mission `work/runs/learnings`,
and mines the principle through `PgCausalEdgeStore.mine_from_mission_loop`, rather than feeding
mocked miner returns. Expected deltas are `+2`, `+1`, `0` (noise), then `-1` (third-environment
counterevidence), ending in an automatic `promoted → demoted` transition.

## Real mission evidence

`artifacts/governed_principle_real_lifecycle.md` records a separate isolated AQ mission executed by
the Codex subscription runner. Its actual `0 → 1` mission cycle was mined through the management
API, cross-tested, promoted, given `0` noise, then contradicted by a `-1` measurement through the
normal live outcome route and automatically demoted. The JSON ledger is preserved beside it.

## Second automatic trigger: governed unproductivity

Contradiction is not required. A promoted rule is also demoted when it was durably selected before
ACT, governed at least **5 resolved plans**, and appeared in **zero goal-reaching chains** inside a
**14-day rolling horizon**, with at least **24 hours** between the first selection and latest
outcome. These defaults are intentionally conservative: five uses reject a one-off miss, fourteen
days retires a persistently useless software rule within a normal iteration, and the minimum span
prevents a retry burst from stripping authority. Operators may configure all three via
`AQ_UNPRODUCTIVE_SELECTION_THRESHOLD` (minimum 3), `AQ_UNPRODUCTIVE_HORIZON_DAYS`, and
`AQ_UNPRODUCTIVE_MINIMUM_SPAN_DAYS`. The resolved policy and counted application IDs are stamped
onto the demotion transition.

Assessment does not count as selection. After DECIDE and the autonomy gate, the loop appends an
immutable pre-ACT receipt with the exact principle identity and promotion transition. Only an
unambiguous promoted governor gets a receipt. The post-measure outcome is a second append-only row
for that receipt. Unknown, replayed, deferred, ambiguous, unresolved, and older-promotion plans do
not count. Any goal-reaching chain in the horizon prevents unproductivity demotion. A reach-and-maintain
mission counts only satisfaction of its declared (and, when configured, live-query) target; a
positive delta alone is not mislabeled as goal attainment. A maximize mission has no terminal
target, so its objectively productive cycle is the goal unit. Provisional rules may remain
provisional indefinitely and accumulate cross-environment experiment evidence,
but they cannot accumulate authority debt before promotion.
