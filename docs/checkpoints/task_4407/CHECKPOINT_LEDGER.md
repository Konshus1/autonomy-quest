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

## Clarifications locked (Kevin 2026-07-31)
- Docker kit: React+FastAPI **working OOTB in image**.
- Prompt kit: React+FastAPI are **defaults**; human may switch FE/BE; agent builds abstract components.
- Cohort→main: **manager-gated** (coding-agent manager approves or escalates to human) — not operator-gated.
