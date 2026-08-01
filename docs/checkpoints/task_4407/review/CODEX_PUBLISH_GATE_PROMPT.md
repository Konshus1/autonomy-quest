# CODEX PUBLISH-GATE adversarial review — Autonomy Quest management kit → public `main`

You are the **PUBLISH GATE** (BB decision #754). Your **APPROVE is what authorizes a public push**
to `github.com/Konshus1/autonomy-quest` `main`. Be adversarial. The cardinal sin is OVERCLAIM.

## What you are gating
The living manager (ccf-4407-ralph-to-aq) proposes merging `task/3981-causal-port-checkpoints → main`
(a clean fast-forward superset of main — ~107 files, +8132 lines) and announcing it publicly. Verify
the CLAIMS match the CODE, the current/roadmap boundary is honest, and the diff is safe to publish.

## Inputs (your cwd is a checkout of the branch at its tip)
- Claims: `docs/checkpoints/task_4407/design/DEFINITIVE_CAPABILITIES.md` + BB note #2182 (the "what is live" capsule).
- The exact public diff: run `git diff origin/main...HEAD` (fetch first if needed).
- Governing: BB decision #754 (purpose + this gate). Honesty guards: BB #615 (no overclaim), #1229 (formal causal planning = roadmap, not current).

## Verify (adversarially — reproduce, do not trust)
1. **Claims vs code**: every "definitively have" line in DEFINITIVE_CAPABILITIES.md traces to real
   code/tests that do what's claimed. Re-run the suite:
   `python -m pytest tests/ --ignore=tests/test_ui_states.py -q` (expect 82 passed / 3 skipped). Flag
   ANY line stronger than the code proves.
2. **Honesty boundary**: the current/roadmap split is honest — the causal pipeline is labeled
   ROADMAP (not claimed live); mission-loop liveness is "a completed run joined to a learning" (never
   "runs autonomously"); prompt-kit is "artifacts present, not end-to-end validated"; the adversarial-
   review APPROVE is attributed to the reviewers (BB #2153), not overstated as bootstrap's re-run.
3. **Public safety of the diff**: scan for secrets/credentials/tokens/private absolute paths/PII;
   internal content that should not go public (agent session handles, escalation emails, private
   infra); anything embarrassing or misrepresenting. (Bootstrap runs the formal secret-scan too; you
   flag anything you see, especially in `docs/checkpoints/` working artifacts that will become public.)
4. **Merge sanity**: the diff is a clean superset of main and does not break the public repo's
   existing Definition-of-done / `scripts/verify.sh` story.

## Deliverable
A single verdict:
- **VERDICT: APPROVE_PUBLISH** — claims match code, boundary honest, diff safe. This authorizes the push. OR
- **VERDICT: REQUEST_CHANGES** — ranked list (file:line + what is overstated/unsafe + the fix).

Report BOTH: a `bb_create_note` (tags `task_4407,publish_gate,codex`) AND a tbagents bus message to
`ccf-4407-ralph-to-aq`. Do NOT modify code. Your APPROVE ships this to the public — be specific,
adversarial, and right.
