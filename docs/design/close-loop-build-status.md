# Close-the-loop build status

**Build:** `task/close-loop-enforced` (evaluable shadow chain and hardened authorization added)
**Authority:** public main disabled; no public push implementation exists

## Green locally

- Phase-0 real Git worker commit, distinct authenticated exact-SHA detached review, deterministic refusal gate, and AQ-local no-ff integration ref.
- A pure `close_the_loop` consultant implements the frozen `#4834.details.shared_seam_contract`
  recommendation shape without owning self-correction or the shared arbiter.
- `scripts/run_close_loop_shadow.sh` runs one admitted queue item through fenced materialization,
  a real worker branch, the consumed hermetic verifier, authorization and merge gates, and an
  append-only hash-chained shadow receipt. It proves public actuation remains refused.
- Hermetic Docker verifier and external trusted manifest closure. Run `scripts/run_close_loop_controls.sh`; Docker absence is a rig failure, not a skip.
- Hermetic verdict authorization binds repository/full SHA, test-plan digest, approved image and
  entrypoint identities, effective-policy digest, key ID, expiry, and replay nonce. `PASS` requires
  `passed_count > 0`; all-skipped/vacuous runs and unexpected container environment entries hold.
- `AQ_BRIDGE_MODE=observe|materialize|dispatch` exact-mode boundaries. Observe is the default and can write only shadow observations.
- Fenced shared lease schema and local AQ/Ralph selector adapters, immutable source-intent and mission-boundary hashes, and mission-loop exclusion of `worker_reviewer` work.
- PostgreSQL schema 001+025 applies idempotently twice on a clean PG15 database.

## Acceptance hold

The CRITICAL shared-CAS proof is not complete until TalkingBack's production
`task_priority_selector` calls the same checked lease primitive and the race test executes that real
entry point against AQ's real selector. The AQ-local `RalphSelector` is a concrete adapter and useful
state-machine proof, but it is not a substitute for the deployed Ralph selector.

Consequently the public actuator hard gate remains closed and Track-B is not requesting c2f/Kevin
enablement sign-off. No status flag or local green may override this hold.

## Validation receipts

```text
scripts/run_close_loop_shadow.sh
prints aq.close-loop.shadow-receipt.v1 with gate.approved=true and public_actuator.status=refused

scripts/run_close_loop_controls.sh
CLOSE-LOOP-SANDBOX OK: 4/4 mandatory controls passed

python hermetic_verifier/prove_controls.py
HERMETIC_CONTROL_PASS plus six BLOCKED/ALLOWED contrast capabilities
```

The repository-wide skips are pre-existing optional/integration tests; the dedicated close-loop
security harness has an exact expected count and refuses skip, xfail, deselection, collection error,
or missing Docker.
