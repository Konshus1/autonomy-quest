# REVERT-DISCRIMINATE (close-the-loop gate, Check 1)

A self-contained, advisory primitive for the grounded-evaluator gate
(`docs/design/close-loop-grounded-evaluator-gate.md` -> "Discriminate / revert").

## What it decides

Given a candidate change (`base_sha` -> `candidate_sha`) and the candidate's
supplied tests, it answers one mechanical question: **do those tests FAIL when
the change under test is reverted?** A suite that passes with and without the
change does not test the change; a zero-real-tests forgery discriminates
trivially-not. This is the red-first discipline moved from the manager into the
gate, and it is the check a forged-census candidate cannot pass — it has no
genuine test that flips on revert.

The result is **advisory input to the evaluator, not a standalone verdict**.

## The mechanic

1. `changed = diff(base, candidate)`, split by `is_test_path` into **non-test**
   files (the change under test) and **test** files (supplied tests).
2. **Tree A** = the candidate tree, verbatim.
   **Tree B** = the candidate tree with the *non-test* change reverted to base
   content, tests kept exactly as supplied (so a flip stays observable).
3. Run the supplied test target against both trees, read per-test outcomes from
   pytest junit. The change **discriminates iff at least one supplied test PASSES
   on A and does NOT pass on B**.

Output shape (`DiscriminateResult.to_dict()`):

```json
{
  "discriminates": false,
  "tests_flipping": [{"test_id": "...", "outcome_candidate": "passed",
                       "outcome_reverted": "failed", "kind": "assertion_failure"}],
  "tests_total": 0,
  "notes": ["no_supplied_tests_collected"],
  "strong_flips": 0, "weak_flips": 0,
  "changed_non_test_files": ["..."], "changed_test_files": ["..."]
}
```

## Files

- `runner/close_loop/revert_discriminate.py` — the primitive.
- `tests/test_close_loop_revert_discriminate.py` — the red-first tests.

## Execution / containment

Test *execution* is delegated to an injected `TreeTestRunner`:

- `LocalPytestRunner` (default) — runs pytest as an ordinary host subprocess.
  Provides **no** containment; used by the unit tests (containment is not the
  property under test) and for trusted local use.
- `SandboxedPytestRunner` — the **production** path. Reuses
  `runner/close_loop/sandbox.py::run_in_sandbox` **unchanged**: the tree is
  mounted read-only, no network, no capabilities, no writable host mount; junit
  is returned framed on stdout (never via a writable bind). This primitive reads
  the discriminate *structure* of the two already-contained runs; it does not
  re-implement or weaken the powerlessness boundary.

## What it handles vs. punts

Handled (see the module docstring and tests):
- **Error != pass, ever.** A flip *requires* a genuine `passed` on the candidate
  tree; an error/failure there can never be a flip (no masquerade-as-pass).
- **Error-on-revert** (the change removed a symbol the test uses, so the reverted
  tree cannot even run the test): a *weak* flip by default (`kind="error"`,
  `outcome_reverted` `error`/`collection_error`) — the test provably cannot run
  without the change. `require_clean_failure=True` demands a real assertion
  failure and excludes these.
- **Change hidden inside a test file** — never reverted, so nothing flips and the
  candidate simply *fails* this check. Fail-closed by construction.
- **Change touches no non-test files** — reverting nothing yields Tree B == Tree
  A; `discriminates=False` with note `change_touches_no_non_test_files`.
- **Candidate-deleted / renamed source** — reverted via base-content restore
  (`--no-renames` splits a rename into delete+add).

Punted (documented, evaluator-weighted, not gate-fatal because advisory):
- **Flakiness / order-dependence.** Each tree is run once; a non-deterministic
  test may flip or not by luck. Side-effect coupling between tests is not detected.

## Reproduce

```bash
cd /Users/kevincthomas/src/aq-wt-revert-disc

# 1. The full new suite (host runner; no Docker needed):
python -m pytest tests/test_close_loop_revert_discriminate.py -q
#   -> 11 passed, 1 skipped   (the skip is the live-Docker sandbox test)

# 2. The whole repo still green (re-count per pytest.ini: no lost tests):
python -m pytest -q
#   -> 576 passed, 50 skipped

# 3. (optional) live sandbox integration — needs Docker + the sandbox image:
AQ_RD_SANDBOX=1 python -m pytest \
  tests/test_close_loop_revert_discriminate.py::test_discriminate_under_sandbox_runner -q
```

## Red-first evidence

Each behavioral test is written to FAIL against a naive implementation that
*trusts a pass count* ("the supplied tests pass on the candidate ->
discriminates") instead of reading the fails-on-revert structure. Swapping in
that naive `discriminate()` and re-running fails exactly the tests that encode
the discipline:

```
FAILED  test_zero_real_tests_does_not_discriminate          (forged census)
FAILED  test_noop_tests_do_not_discriminate                 (pass both ways)
FAILED  test_error_on_revert_is_weak_flip_and_strictable    (error handling)
FAILED  test_error_on_candidate_never_masquerades_as_pass   (masquerade guard)
FAILED  test_change_only_in_test_files_does_not_discriminate(fail-closed)
FAILED  test_mixed_suite_reports_only_the_discriminating_test
```

The three positive-boolean cases (`genuine_change`, `revert_restores`,
`to_dict_shape`) a naive pass-count coincidentally gets right on the boolean —
they pin the *flip structure* the naive version still gets wrong elsewhere
(see `mixed_suite`, which a naive count reports as 2 strong flips instead of 1).

Against the real implementation: `11 passed, 1 skipped`.

## Scope / status

Primitive + tests only. **Not** wired into the live gate; nothing armed.
`SandboxedPytestRunner` reuses the existing sandbox with no changes to its
guarantees.
