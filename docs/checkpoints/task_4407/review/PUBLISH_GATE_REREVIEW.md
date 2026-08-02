# CODEX PUBLISH-GATE re-review — CURATED commit `publish/mgmt-kit`

You are the PUBLISH GATE (BB decision #754). Your APPROVE authorizes a public push to
`github.com/Konshus1/autonomy-quest` `main`. A prior codex review returned REQUEST_CHANGES on the
raw branch (internal-artifact/history leak, a test-count overclaim, whitespace). Those are addressed
by publishing a CURATED, SQUASHED commit instead of merging the raw branch. Verify the curated
artifact — NOT the raw branch.

## The artifact under review
`publish/mgmt-kit` — ONE squashed commit off `origin/main` whose tree = the branch MINUS
`docs/checkpoints/`. Diff to review: `origin/main..publish/mgmt-kit`.

## Run (from your cwd; `git fetch origin` first)
1. `git diff --stat origin/main..publish/mgmt-kit` — confirm code+tests only, NO `docs/checkpoints/`.
2. `git diff origin/main..publish/mgmt-kit | grep -nE '^\+.*(X-Internal-Service-Token|/Users/kevincthomas|escalate_to_kevin|AGGIE_EMAIL_PASSWORD|aq:x@localhost:5549)'` — expect ZERO added sensitive lines.
3. `git diff --check origin/main..publish/mgmt-kit` — expect clean (no whitespace/EOF).
4. `python -m pytest tests/ --ignore=tests/test_ui_states.py -q` — expect all pass/skip, no failures.
5. Spot-check that the DEFINITIVE_CAPABILITIES claims (BB #2182) still hold against the curated code
   (they are honest: causal pipeline = roadmap; mission-loop liveness = a completed run joined to a
   learning; prompt-kit = artifacts only; reviewer approvals attributed to reviewers).

Note: any `/Users/...` hits under `artifacts/task_3303/*` are PRE-EXISTING in `origin/main` (confirm
`git diff --name-only origin/main..publish/mgmt-kit` does NOT list them) — not introduced here.

## Deliverable
Reply on the tbagents bus to `ccf-4407-ralph-to-aq`:
- **VERDICT: APPROVE_PUBLISH** (curated diff is clean + claims-accurate) — authorizes the push. OR
- **VERDICT: REQUEST_CHANGES** (ranked). Do not modify code.
