# Task 3303 Completion Packet

worker: cxf-3303-approve-execute-auth-ceiling
branch: fix/approve-execute-auth-ceiling
base: 038a8962e11289af0eced48c8c18eaf6c715a471
status_recommendation: branch-ready-for-bootstrap-review, not merged
worker_stop_claim: implementation, focused tests, RED/GREEN evidence, syntax checks, and completion packet are complete; Docker/browser e2e certification remains bootstrap-owned
confidence_pct: 88
semantic_completion_strength: strong for F1/F2/F3 unit and structural coverage; medium for container runtime because full Docker e2e was intentionally not claimed

## Files Changed

- .env.example
- container/README.md
- container/entrypoint.sh
- docs/doctrine.md
- docs/mission-status-ui-spec.md
- install.sh
- runner/approval.py
- runner/db.py
- runner/loop.py
- scripts/verify.sh
- scripts/verify_config.py
- setup.md
- tests/test_approval.py
- tests/test_loop_approval_execution.py
- tests/test_ui_approval_auth.py
- tests/test_ui_states.py
- tests/test_verify_config.py
- ui/server.py
- artifacts/task_3303/fail_first_inspection.md
- artifacts/task_3303/f1_base_red.txt
- artifacts/task_3303/blocker_failure_path_red.txt
- artifacts/task_3303/blocker_failure_path_green.txt
- artifacts/task_3303/completion_packet.md

## What Changed

F1 approve->execute:
Approved parked work is now selected by `approved_at` before optional curiosity or any new model decision. The loop validates the selected row through `runner.approval.assert_valid_approval()` and executes it through the shared `execute_work()` act -> record -> learn path.

Approved failure path:
If human-approved work fails during execution, the run is recorded terminally failed and the work is re-parked with `approved_at` cleared. It cannot be selected as approved work again without a fresh human approval. If approved work is interrupted by rate limit, the approval is also cleared and the work is re-parked, bounding retry instead of looping forever.

F3 approve-auth:
`/api/approve/{id}` now fails closed when `AQ_APPROVAL_TOKEN` is unset, rejects missing or wrong tokens, accepts `Authorization: Bearer <token>` or `X-AQ-Approval-Token`, performs only the guarded `awaiting_human -> pending` transition, and validates the returned row with the same approval invariant. Native install and container startup generate an approval token only when absent; no hardcoded token is introduced.

F2 maximize-ceiling honesty:
`verify.sh` now delegates mission validation to `scripts/verify_config.py`. `reach_and_maintain` requires a target/target_query and may use ceiling language. `goal:maximize` is accepted only with a positive hard spend cap and no longer passes or prints as if it has a mission ceiling.

Doctrine:
`docs/doctrine.md` now has invariants 5/6/7 for approval, red-first tests, and output liveness, pointing to structural enforcement. The older UI spec and setup/container docs were updated to match token-gated approval.

## Fail-First Evidence

- Preserved pre-edit inspection artifact: `artifacts/task_3303/fail_first_inspection.md`
- Actual F1 RED on base `038a8962e11289af0eced48c8c18eaf6c715a471`: `artifacts/task_3303/f1_base_red.txt`. The focused test saw `cycle is None` instead of executing work #42.
- GREEN proof: `tests/test_loop_approval_execution.py::test_approved_pending_work_executes_before_deciding_new_work` proves approved pending work executes via ACT/REFLECT before DECIDE; `test_unapproved_pending_work_is_not_treated_as_human_approval` proves plain pending work is not treated as approval.
- Blocker RED on `c18c457` behavior before this follow-up fix: `artifacts/task_3303/blocker_failure_path_red.txt`. The same approved work was selected and ACTed again after failure.
- Blocker GREEN after fix: `artifacts/task_3303/blocker_failure_path_green.txt`. The failure ACTs once, records failure, clears `approved_at`, re-parks work, and the next cycle does not re-execute it.

## Commands And Results

- `./.venv/bin/python -m unittest -v tests.test_approval tests.test_verify_config tests.test_loop_approval_execution tests.test_ui_approval_auth` -> PASS, 13 tests.
- `./.venv/bin/python -m unittest discover -v` -> PASS, 27 tests.
- `python3 -m py_compile runner/approval.py runner/db.py runner/loop.py ui/server.py scripts/verify_config.py tests/test_approval.py tests/test_verify_config.py tests/test_loop_approval_execution.py tests/test_ui_approval_auth.py tests/test_ui_states.py` -> PASS.
- `bash -n scripts/verify.sh install.sh container/entrypoint.sh` -> PASS.
- `git diff --check` -> PASS.
- `./.venv/bin/python scripts/verify_config.py <maximize-no-cap fixture>` -> expected failure `missing|maximize_without_spend_cap|...`.
- `./.venv/bin/python scripts/verify_config.py <maximize-with-cap fixture>` -> expected success `ok|maximize_spend_cap|...`.
- `./.venv/bin/python -m unittest -v tests.test_loop_approval_execution.LoopApprovalExecutionTests.test_failed_approved_work_is_reparked_not_reexecuted_next_cycle` -> RED before fix captured in `blocker_failure_path_red.txt`, GREEN after fix captured in `blocker_failure_path_green.txt`.
- Base-worktree run of `tests.test_loop_approval_execution.LoopApprovalExecutionTests.test_approved_pending_work_executes_before_deciding_new_work` against `038a8962e11289af0eced48c8c18eaf6c715a471` -> expected RED captured in `f1_base_red.txt`.

Not run:
Full Docker browser approval e2e was not run and is not claimed. Bootstrap owns Docker e2e certification.

Agent review:
The `agent-review` skill was read before this packet. Its canonical `scripts/agent-review` script is not present in this public-kit repo, and `CODEX_HOME` is unset for native `codex review`; no private TalkingBack/Ralph dependency was introduced to compensate.

## Remaining Work Items

- Bootstrap: run full Docker/browser approval flow and Windows prep certification.
- Reviewers: ccf-1557-claude-takeover for approval-gate doctrine and cxf-ralph-advisor for advisor review.
- Operator docs may later improve UX around entering/storing the approval token, but the structural auth gate is in place.

## Human Boundary

No merge, deploy, publish, release, push to main, or visibility change was performed. Kevin/bootstrap retain merge and release gates.

## Recommended Followups

- Bootstrap review should confirm the generated token appears in container logs and that a browser approval with that token causes the parked work to execute in a full container run.
- Consider a future DB-level partial index or helper to claim one approved row atomically if multiple loop processes are ever supported. Current single-loop assumptions match the existing supervisor model.

## Next Best Action

Bootstrap design/e2e review on this branch, then ccf-1557 and advisor review. Recommended status remains `branch-ready-for-bootstrap-review`, not merged.
