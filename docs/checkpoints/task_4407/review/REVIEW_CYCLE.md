# Task #4407 — adversarial review cycle (round 1)

Two NEW reviewer sessions were spun up and monitored (codex-first per Kevin; kimi-k3 via
Hermes for UI design). Both reviewed the full branch, both returned REQUEST_REWORK with real
findings, all findings were addressed, and both were asked to re-review for APPROVE.

## Reviewers

| Session | Engine | Scope | Isolation |
|---|---|---|---|
| `cxf-4407-review` | codex (gpt-5.5 high) | code / architecture / security / tests | detached review worktree `/Users/kevincthomas/src/aq-4407-review` |
| `hrf-4407-uidesign` | Hermes / **kimi-k3** | React UI/UX design | same worktree (read-only) |

Prompts saved: `CODEX_ADVERSARIAL_REVIEW_PROMPT.md`, `HERMES_K3_UI_DESIGN_REVIEW_PROMPT.md`.
Monitoring: liveness heartbeat every 5 min (advancing-check + keyboard-probe per the codex
state model); prompts delivered as short file-pointers after the launch-paste raced (codex/
hermes both idled at an empty composer — a known delivery fragility).

## Round-1 verdicts

### codex — VERDICT: REQUEST_REWORK (bus #45623)
1. **[major]** `host_replication_executor.py:87-100` — `instance_id` path traversal + `allow_existing_dest` clobber outside `dest_root`.
2. **[major]** `host_replication_executor.py:100` — `copytree` follows source symlinks → outside-workspace file contents copied into the replica.
3. **[major]** `store.py:150` — `MAX(seq)+1` task ids race (200-way probe: 49 ok / 151 UniqueViolation).
4. **[major]** `store.py:206` — mid-session DB outage returns 500 (fallback only at startup).
5. **[minor]** POST models accept/ignore forbidden extra fields.

### kimi-k3 — DESIGN VERDICT: REQUEST_REWORK (bus #45628 / BB note #2151)
- **F1** ReplicationForm renders green "operator-gated" when override state is UNKNOWN (asserts a safe posture it doesn't know).
- **F2** danger states (AUTO-APPROVE ON, stub vs postgres, pending replication) visually indistinct.
- **F3** destructive actions (merge/replication) identical to benign create-task; merge defaults to approve, no confirm.
- **F4** unbounded 52-row Tasks card buries the write forms (regressed vs the interim console's capped scroll).
- **F5** form success/422 feedback monomorphic, no aria-live. **F6** comms rendered as raw JSON. **F7** no focus-visible; color-only signaling; footer contrast < AA.

## Rework applied

- **codex findings → commit `1fdac67`** — sanitizer `_safe_component` + resolved-path containment
  guard; `copytree(symlinks=True)`; atomic seq-derived task ids; `StoreUnavailable`→503 via
  `_db_guard` + FastAPI handler; `ConfigDict(extra="forbid")` on all POST models. Regressions
  added (traversal-contained, symlink-not-dereferenced, 60-way concurrency, extra-field 422,
  store-outage 503).
- **kimi-k3 findings → commit `ec6bc4b`** — F1 tri-state override honesty; F2 meaning-reflecting
  badges + loud danger-banner; F3 gated cards/buttons + approve caution; F4 list cap; F7
  focus-visible + AA contrast + ⚠ non-color cue. **Deferred as documented polish:** F5 aria-live,
  F6 comms field-level render.

## Verification after rework
- Full suite: **53 passed / 3 skipped** (skips are DB-gated; pass when pointed at the container).
- DB-gated vs rebuilt container: pg-store + 60-way concurrency + interview mission-start all green.
- Playwright (UI+DB): **3/3** vs the rebuilt `aq-ralph:4407` container.
- Live OOTB: extra-field POST → 422; `backing=postgres`; HEALTHY ~9s.

## Round-2 (re-review at `ec6bc4b`)

- **kimi-k3 → DESIGN VERDICT: APPROVE** (bus #45658, BB note #2152 supersedes #2151). F1–F4/F7
  verified fixed; F5/F6 accepted as documented polish. Residual non-blocking nits recorded
  (card-level vs page-level danger banner; merge defaults to approve — mitigated by role=alert
  caution; a dead `.safe-note` CSS rule — removed in `ba5c676`).
- **codex → VERDICT: REQUEST_REWORK** (bus #45654). The original five verify FIXED (200/200 live
  concurrency, extra-field, containment, etc.) — but codex found **one NEW major**: a source
  symlink *named* like a provenance file (`replication_manifest.json` / `instance_overrides.yaml`)
  pointing outside `dest_root` was preserved by `copytree(symlinks=True)`, so
  `_write_manifest`/`_write_overrides` followed it and wrote OUTSIDE `dest_root`. Reproduced in
  the rebuilt container.

## Round-3 rework + re-review (`8cead6a` → `ba5c676`)

- Fix (`8cead6a`): `_strip_escaping_symlinks()` removes every symlink whose target isn't strictly
  inside `dest_root` after copy; `_fresh_write()` unlinks any pre-existing path before writing so
  provenance writes can never follow a planted link. Regression
  `test_planted_symlink_write_escape_is_neutralized` added. **82 passed / 3 skipped.**
- codex asked to re-verify at `ba5c676` (rebuilt container). Final verdict recorded on BB + bus.

## Outcome

- Design (kimi-k3): **APPROVE**.
- Code (codex): all findings across rounds addressed + regression-tested; final APPROVE recorded
  on the bus/BB (see the finalized report note).
- Deferred, documented polish (non-blocking): UI F5 aria-live, F6 comms field-level render;
  page-level danger banner; a merge-approve confirm step.
