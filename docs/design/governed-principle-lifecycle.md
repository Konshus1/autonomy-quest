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
`authoritative=false`. `shadow_guidance()` caps the proposed experiment count at one. Only a
promoted rule carries authority.

Promotion is available through `/api/causal/governance/promote` only when
`AQ_GOVERNANCE_TOKEN` is configured and supplied in `x-aq-governance-token`. The adjudicator must
differ from the recorded miner, two cross-domain supports must exist, and applies-here plus a
negative control are mandatory.

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

The proof executes actual SQL actions and measures in PostgreSQL, rather than feeding mocked
returns. Expected deltas are `+2`, `+1`, `0` (noise), then `-1` (third-environment
counterevidence), ending in an automatic `promoted → demoted` transition.
