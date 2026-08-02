# Autonomy Quest — Reconciled Capabilities Record (anti-overclaim audit)

*Produced by a parallel audit session (2026-08-02), verified by the living manager. Ground-truth basis: working tree `task/3981-causal-port-checkpoints`; public demo `Konshus1/autonomy-quest` `4ac5fff`.*

## 1. Claimable NOW (precise wording)

1. **Causal principles are MINED (fuzzy, with provenance) from the loop's own outcomes.** Completed+succeeded runs whose mission measure moved become candidate **fuzzy** edges (`work.kind → measure_up/down`), with provenance, deduped by immutable identity and by `run_id`, `predicted_certainty` capped at 0.34.
2. **A durable, identity-keyed causal-edge store** (Postgres `ralph_causal_edges`, PK immutable `cause/effect/scope`; in-memory fallback of identical shape). Mutable state rides as jsonb, never in the key.
3. **A READ-ONLY viewer of mined principles** in the React shell: cause→effect, dials, `observed_runs`, earned support (+ expandable prediction→outcome history), provenance. It never authors or promotes edges.
4. **The loop CONSULTS the principles (read-only) to predict** — records an honest pre-act `predicted_certainty`; this scores the plan, it does **not** choose the work.
5. **The loop SCORES outcome vs prediction — a live surprise loop that earns support.** `confirm` increments support; a confident-but-wrong outcome returns a **gated demotion proposal**. Support is *earned*, not observed.
6. **The loop AUTO-PRODUCES principles** each productive cycle.
7. **The whole causal path is best-effort / fail-open** — no consult/score/mine call can fail, delay, or undo a durably-recorded cycle.

**One-sentence claim:** *The loop auto-mines fuzzy causal principles (with provenance) from its own run→learning outcomes, stores them durably, exposes them read-only, consults them read-only to predict each act's outcome, and scores prediction-vs-reality in a live surprise loop that earns support toward a (proposed, never applied) promotion.*

## 2. Roadmap (NOT offered today) — confirmed absent/unwired in code

- **Fuzzy → formal PROMOTION (actuation)** — `propose_update` only *returns* a proposal; **no code applies a formality change**. An edge never leaves `fuzzy` live.
- **Formal planning (PDDL/ASP/proof-carrying plans)** — no such module; only a read-only certainty *profile* is computed; plans are never rejected.
- **Principles GUIDING decisions (consult-act)** — consult is read-only; certainty never chooses/gates/reorders work.
- **A live evaluator / merge loop step** — `manager_merge_gate` exists as validator + API, not wired as a cohort→main orchestrator step.
- **Test-laddered worker/evaluator cohort** — not running.
- **Self-evolving / autonomous replication** — only a propose→host-execute surface; no loop-initiated replication.

## 3. Drift / overclaim audit (fixed)

- **A1 (FIXED, commit 185115f):** viewer copy "Formality is earned by surprise-driven promotion" implied promotion is live. Reworded: "the surprise loop accrues support toward promotion, but promotion is not yet applied (roadmap)."
- **A2 (FIXED, 185115f):** `app.py` causal docstring "surprise-driven gated promote/demote" → clarified it *proposes*, never applies.
- **A3 (watch):** README "changes its own behavior to do better next time" rests on the base learn→decide loop, NOT the causal layer (consult-only). Keep the causal layer out of any "changes its behavior" claim until consult-act lands.

## 4. Stale under-claims to refresh (docs lag code — safe but inaccurate)

- `DEFINITIVE_CAPABILITIES.md` "causal-principle analysis = doctrine only / not running" — now: mining + read-only consult + surprise-scoring **run and are tested**; formal planning + cohort still do not.
- `DESIRED_STATE.md` "Today" column "Causal-principle analysis: doctrine only" / "Learning updates principles: learnings feed decide prompts" — both stale; analysis now has a versioned principle store + live mine/consult, and learnings also mine fuzzy principles.
- **Keep exactly as-is (still correct):** the `DEFINITIVE_CAPABILITIES` honesty guard "formal causal planning is ROADMAP, NOT claimed"; `ROADMAP_GOAL.md`'s end-goal framed as not-yet-achieved; replication/merge bounded as surface-only.

**Bottom line:** the live causal system is a genuine, tested single-agent **mine→consult→predict→score→earn-support** loop over **fuzzy** principles with a read-only viewer. The one load-bearing overclaim risk — wording that implies principles get *promoted* or *guide decisions* — is now corrected in code; everything above formality-promotion remains honestly roadmap.

## THE MINE-VS-CONSULT SEAM (canonical — flagged by the ship-gate, bootstrap 2026-08-02)

The claim boundary MOVED as the loop-wiring slices (2c/2d) advanced past the original causal-front-half ship (which was mine + read-only viewer only). State it precisely everywhere — post text, README, capabilities:

- **SHIPPED (read-only):** the loop MINES principles and now CONSULTS them to **PREDICT** — before acting it reads the governing principle's certainty and records that pre-act prediction, then SCORES it against what actually happened (the surprise loop, earning support). It reads them; it does **not** let them choose the work.
- **ROADMAP (not shipped):** consult-to-**DECIDE** (principles actually influencing/gating/reordering which work is chosen — "consult-act"); fuzzy→formal PROMOTION actuation; formal planning.

**Do NOT say "I do not consult them"** — that is now false; the loop consults them to predict. The true, honest line is: *"I now read them to SCORE my plans (a read-only prediction I record before I act and check against reality) — but I don't yet let them CHOOSE my work or treat them as rules; that's roadmap."* Any public post or doc using the old "doesn't consult" phrasing must update to this seam before/with the loop-wiring ship.
