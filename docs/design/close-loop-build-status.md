# Close-the-loop build status

**Build:** `task/close-loop-enforced` at `7362d29`
**Authority:** public main disabled; no public push implementation exists

## Green locally

- Phase-0 real Git worker commit, distinct authenticated exact-SHA detached review, deterministic refusal gate, and AQ-local no-ff integration ref.
- Hermetic Docker verifier and external trusted manifest closure. Run `scripts/run_close_loop_controls.sh`; Docker absence is a rig failure, not a skip.
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
pytest -q
506 passed, 49 skipped

scripts/run_close_loop_controls.sh
CLOSE-LOOP-SANDBOX OK: 4/4 mandatory controls passed
```

The repository-wide skips are pre-existing optional/integration tests; the dedicated close-loop
security harness has an exact expected count and refuses skip, xfail, deselection, collection error,
or missing Docker.
