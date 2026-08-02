# Autonomy Quest — Definitive capabilities (what we have TODAY, evidence-backed)

> **Second-party accuracy gate: PASS** (ccf-kevin-bootstrap, verified @ `fb5993f`) — every have-line
> traces to re-run evidence or is explicitly attributed to its owner; nothing overstated.
> Still requires the standard AQ publish gates (honest-tier + secret scan on the exact diff) and the
> explicit Kevin + bootstrap + ccf-4407 gate before any public use.

Independently validated by **ccf-kevin-bootstrap** on branch `task/3981-causal-port-checkpoints`
@ `8acaba9`, image built from that tree (bootstrap's build tagged `aq-ralph:4407-validate`, 869MB).
A line appears here ONLY if it has a reproducible test/demo that bootstrap re-ran on their env —
except where a cell explicitly attributes a claim to another party's evidence.

**Honesty guard** (BB #615 held-out overclaim guard; BB #1229 — formal causal planning is out of
v1 claims): the causal-formality workflow is **ROADMAP** (BB decision #746), **NOT claimed here**.

## A. Definitively have — validated, tested
| # | Capability | Evidence (bootstrap re-ran) |
|---|---|---|
| 1 | One-`docker run` OOTB infra+management image | build 869MB → `run -e POSTGRES_PASSWORD=x` → **HEALTHY ~6s**; Postgres+AGE substrate |
| 2 | React management shell served OOTB | `:8090/` serves the built React shell (`id=root`, hashed JS, no interim marker); Playwright render test |
| 3 | FastAPI management API + **durable Postgres** state | `/api/ralph/state backing=postgres`; PgStore integ + concurrency 8 passed; Playwright: **create-task through the UI persists to Postgres**, survives reload, reflected in the DB-backed API |
| 4 | Durable tasks + agent-comms surfaces | comms POSTed via API renders in the UI; management_store/comms tests |
| 5 | Manager-gated merge **gate** (`approve_merge \| escalate_human`) | `/health merge_gate=manager_gated`; `manager_merge_gate` module + tests |
| 6 | Operator-gated, **host-executed** replication + host OVERRIDE | `override_enabled=False` OOTB (operator-gated default); host_replication_executor + broker + adversarial tests (path-traversal/symlink). Boundary: **guest PROPOSES, host EXECUTES, guest never gets docker.sock**; host OVERRIDE via `AQ_REPLICATION_AUTO_APPROVE` |
| 7 | Single-agent **mission loop** — a completed run joined to a learning | GROUND TRUTH: with a live DB the aimed loop recorded work=1, runs=1, learnings=1 with **1 completed run JOINED to a learning** — the output-based liveness bar (never "it runs autonomously") |
| 8 | Prompt-only kit **artifacts present** | interview pack 00–12 (13 files) + abstract-requirements doc. **ARTIFACT PRESENT ONLY — end-to-end reproduction by an agent is NOT yet validated** (no agent followed 00–12 from scratch to rebuild the system). The OOTB Docker image is the runtime-verified path |
| 9 | Adversarial-review **regression tests** pass | the review findings' regression tests pass — incl the symlink-write-escape (`test_source_symlink_escaping_workspace_is_stripped`, 12/12) — **bootstrap re-ran**. *Separately, and NOT a bootstrap re-run:* the codex + kimi-k3 review verdicts (both APPROVE) are the **reviewers'** evidence — BB #2153 |
| 10 | Test surface | 82 passed / 3 skipped (#4407 suite) + Playwright 3/3 + interview mission-start; **independently reproduced by bootstrap** |

## B. The honest boundary — built + tested SURFACES/GATES/DOCTRINE, not a running orchestrator
- Manager-gated merge is a **gate + API + doctrine**, tested — **not** an autonomous multi-agent cohort→main flow.
- Replication is a **propose→host-execute broker/surface**, not loop-initiated; materializes a workspace copy (no replica container spin-up yet).
- Causal-principle analysis + formal planning + test-laddered worker/evaluator cohort = **doctrine + first-slice-building**, not running.
- The single-agent mission loop **executes cycles and records a learning-joined outcome (validated)**; the **multi-agent causal pipeline does NOT run yet**. Do not state "runs autonomously."

## C. Roadmap (NOT claimed) — BB decision #746
Causal-principled autonomy workflow: the causal-edge model (three dials: formality, strictness,
directness), fuzzy→formal ladder (PDDL = directness/executable top, ASP = strictness/constraint
top), surprise-driven gated promotion, multi-agent test-laddered cohort, manager-gated cohort→main,
autonomous replication. Core-Ralph back-half (ccf-1557 Lane C) is partially live — state it ONLY
via ccf-1557 (C1 live, C2 accepted-await-deploy, C3 pending).

## Validation provenance
Independently reproduced by ccf-kevin-bootstrap @ `8acaba9`, image `aq-ralph:4407-validate`; items
1–5 with output (BB bus thread). Pre-existing `tests/test_ui_states.py` (stdlib-UI harness, out of
#4407 scope) fails only for lack of its `127.0.0.1:55433/autonomy_quest` test DB → honest skip-guard
scoped as task **#3458** (skip on DB-unreachable with a visible reason; a real failure still goes RED).

## Publish discipline (before any public use)
Second-party factual gate (ccf-kevin-bootstrap): every have-line traces to an evidence item; no
line stronger than the test proves; roadmap/built boundary honest. Then standard AQ publish gates
(honest-tier + secret scan on the exact diff). No public push without the explicit
Kevin + bootstrap + ccf-4407 gate.

## Mine-vs-consult seam (canonical — ship-gate reconciliation 2026-08-02, mirrors CAPABILITIES_RECONCILED §40-47)
The claim boundary advanced when the loop-wiring slices (2c/2d) shipped past the original mine-only front-half:
- **SHIPPED (read-only):** the loop MINES principles and CONSULTS them to PREDICT — it reads the governing principle's certainty, records a pre-act prediction, and SCORES it against the re-read real outcome (a live surprise loop; a `confirm` earns support). It **reads** them; it does **not** choose the work. It also records a manager-gated merge DECISION (approve/escalate) — a recorded decision, **not** an autonomous git merge.
- **ROADMAP (not shipped):** consult-to-DECIDE (principles influencing / gating / reordering the work chosen — "consult-act"); fuzzy→formal PROMOTION actuation (promotion/demotion is only ever *proposed*, never applied live); formal planning.
- **Do NOT say "does not consult"** — now false. True line: *"reads them to SCORE my plans (read-only, checked against reality); does not yet let them choose my work or treat them as rules."*
