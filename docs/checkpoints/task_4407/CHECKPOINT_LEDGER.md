# Task #4407 — Ralph→AQ dual-kit checkpoints

Epic under #2571. Cursor goal-persistence lane. BB note #2141.

| Version | Date (UTC) | What | Result |
|---|---|---|---|
| **v0-validate** | 2026-08-01T03:10Z | Narrative ↔ BB ↔ code validation; epic #4407 filed | PASS |
| **v1-workflow-map** | 2026-08-01T03:20Z | Bootstrap-consumable `WORKFLOW_MAP.md` + interview 08–12 Ralph-control pack + setup.md index | PASS (docs) |

Next candidates:
1. **v2-instance-schema** — `instance.yaml` schema fragment + example for `ralph_control:`
2. **v3-docker-overlay-plan** — React+FastAPI management overlay composition vs stdlib substrate (decision needed if this becomes default)
3. **v4-replication-protocol** — request JSON schema + host approval/override harness (propose-only)
4. **v5-portable-gates** — port next TB gate slice (test ladder / intent adequacy) as fixture-tested portable module

## Human decisions that may block progress
- Whether React+FastAPI becomes a shipped overlay profile vs staying prompt-built only (tensions with AQ stdlib-only UI doctrine).
- Whether any instance may ever auto-merge cohort→main (TB still human-gated; Kevin wants it eventually).
