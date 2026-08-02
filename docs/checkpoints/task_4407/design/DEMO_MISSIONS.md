# Self-writing-playbook DEMO MISSIONS — converged set

*Produced 2026-08-02 by an engine-diverse actor–critic loop (kimi-k3 via Hermes + Grok-4.5 via Cursor), 2 rounds of adversarial cross-critique, synthesized by the living manager. Both engines independently converged on the SAME shortlist after critiquing each other — convergence-from-diversity (cf. BB #561).*

## The point
Each mission is paired with a **public GitHub repo** that doubles as a live, auditable demo of the causal system pursuing a mission and improving at it — a "self-writing playbook with receipts." Repos are labeled AI-agent test beds. Multiple agents cooperate via reviewed PRs; the manager gates merges; the causal loop mines fuzzy principles from what actually moved the number.

## The two design laws both engines converged on (the hard-won part)
1. **One measure per mission, read from a real artifact** (never a model's self-report, never a sum of 3 signals wearing a trench coat).
2. **The mission number must GROW on real work, not drive to zero.** A "failures → 0" target is a merge *gate*, not a mission — once you hit 0 the causal loop starves (no more "X moved the number" cycles). Prefer a monotonic growth measure; keep failures-at-0 as the gate. *(Manager synthesis of the sharpest disagreement: kimi's "guardrail vs gradient" lens wins for a causal loop that mines upward movement.)*

Plus the anti-gaming guards each engine forced onto the other (below).

## The converged missions

### Mission 1 — Mutation Kill-Rate  *(flagship principle-miner)*
- **Repo:** a tiny, genuinely useful Python utility (~600–1000 LOC, MIT) — e.g. robust JSONL/CSV streaming or LLM-pipeline text-chunking. README: "Public AI-agent demonstration test bed, maintained by Autonomy Quest."
- **Measure (single, scored source ONLY):** nightly/post-merge full run — `mutmut run --paths-to-mutate=src/` → `killed / (total − equivalent)` from the CI artifact. **PR-scoped runs are a queue hint only, never the scored number.**
- **Anti-game guards (both engines required these):** `equivalent_mutants.md` edits are a **manager-only** merge path (workers propose, can't self-merge the denominator); **`public_api_symbols` and `src_loc` must be NON-DECREASING** on mission PRs (blocks the ratio cheat of deleting branches/features to raise %); timeout/suspicious mutants count as *survived* until proven.
- **Why the principles are non-gameable + rich:** you cannot kill a mutant with an assertion-free test; the work queue (surviving-mutant list) is tool-generated and complete; interventions are naturally *typed* (property-based vs example tests, boundary cases, exception-type asserts), giving the miner **categories of X to compare** — e.g. *"hypothesis properties on parsing code kill ~2–3× more mutants per PR than example-based tests."*

### Mission 2 — Fence-Failure Docs / "Agent Ops Field Guide"  *(public face + dogfood)*
- **Repo:** a real mini-curriculum / docs site (MkDocs) whose audience is *agent operators* — i.e. AQ's own audience, so the manager's causal work genuinely pursues it (criterion 7). Every code fence is extracted + executed in CI against a pinned env + recorded fixtures (no live internet).
- **Measure (single primary):** `green_fence_count` (monotonic — fences that pass **with expected-output asserts**, not mere exit-0), from `python scripts/run_fences.py --expect-output --report=json`. `fence_failures` target-0 is the gate.
- **Anti-game guards:** `fence_count` **non-decreasing** (can't win by deleting examples); each fence maps to a chapter-concept checklist entry (kills trivial `print("hi")` fence-pumping); link-check (`lychee`) + documented-symbol-drift are **separate dashboards, NOT in the mission number** (kills sum-farming); checker/fixture versions pinned, bumps are explicit mission amendments.
- **Example principle:** *"a shared prelude setup module → more first-chapter greens per PR than per-fence inline setup."*

### Mission 3 — Quarantine → Accepted Fixture Hardener  *(equilibrium cycle density)*
- **Repo:** a small real CLI (`frontmatter-guard`, or an OpenAPI-example checker) with an expanding adversarial fixture corpus.
- **Measure (single):** `accepted_corpus_count` growth with `accepted_corpus_failures` at 0 gate — `python -m tool corpus check --path corpus/accepted`.
- **Anti-game guards:** accepted count **non-decreasing**; a quarantine fixture is promoted to `accepted/` **only in the same PR as the fix** that keeps failures at 0 (no free promotions — fixtures enter quarantine as *recorded-failing*); new fixtures must cite a real-world pattern (manager rejects absurd junk); quarantine size is NOT the measure.
- **Why:** you can't lower the number by emptying `accepted/` or parking bugs in quarantine; each promotion is one clean causal event; density doesn't depend on upstream churn (unlike pure docs-drift).

## Shared infrastructure (NOT a mission) — Honest Measure Adapters
Both engines proposed/critiqued this into its right role: the **adapter layer** that reads each mission's number from its real artifact (mutmut json, fence-runner json, corpus count) and hands it to the loop — dogfooding AQ's own "re-read the measure from source, never self-report" discipline. **Build it first as the plumbing under all three missions; it is not a headline mission itself** (its measure floors at 0 and its audience is agents, not people).

## Manager recommendation (build order)
1. **Honest-measure adapter layer** (the plumbing) — smallest, unblocks everything.
2. **Mission 1 (Mutation Kill-Rate)** as the flagship — the receipts are the hardest to argue with for the public "this agent measurably gets better, with receipts" claim.
3. **Mission 2 (Agent Ops Field Guide)** as the public face — genuinely useful, AQ's own audience, best outsider story.
4. **Mission 3 (Fixture Hardener)** when a third repo is affordable, for post-saturation cycle density.

## Rejected / demoted (both engines agreed)
Coverage-% and good-first-issue (gameable / externally-paced) — dead as primary measures. Lighthouse a11y (saturates + aria-spam-gameable) — out. Time-to-Green — a strong *first-arc* visceral demo only (use `friction_count` as primary), never the permanent engine once it saturates.
