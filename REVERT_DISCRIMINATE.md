# REVERT-DISCRIMINATE (close-the-loop gate, Check 1)

A self-contained, advisory primitive for the grounded-evaluator gate
(`docs/design/close-loop-grounded-evaluator-gate.md` -> "Discriminate / revert").

## What it is: a fail-closed NEGATIVE FILTER

Given a candidate change (`base_sha` -> `candidate_sha`) and the candidate's
supplied tests, it answers one mechanical question: **does at least one supplied
test PASS on the candidate and NOT pass when the change under test is reverted?**

The result is a **necessary-but-not-sufficient** advisory signal, not a verdict:

- **`discriminates=False` is sound and valuable.** If no supplied test changes
  outcome when the change is reverted, the supplied tests do not test the change.
  This kills the zero-real-tests forgery, no-op `assert True` suites, and
  tests-only changes. A `False` here is a trustworthy "this did not test the
  change."
- **`discriminates=True` is necessary, NOT sufficient.** It proves a test's
  outcome *depends* on the change — nothing more. It does **not** prove the test
  asserts anything behaviorally meaningful, and this primitive **cannot** prove
  that.

> **Evaluator contract (in the payload as `necessary_not_sufficient: true` and
> note `discriminates_true_is_necessary_not_sufficient__evaluator_must_read_flipping_test_source`):**
> when `discriminates=True`, the de-correlated evaluator MUST read the SOURCE of
> each test named in `tests_flipping`, together with the diff, and judge whether
> the test meaningfully asserts the changed behavior. This mechanical primitive
> is the cheap, sound pre-filter in front of that judgment; it never stands in
> for it.

## Why `True` cannot be made sufficient (the census limit, again)

The candidate authors the test. A prior version graded a flip as "strong" (a
real assertion) vs "weak" (mere exception coupling) by classifying the on-revert
failure. That is **unwinnable** and has been removed:

- Reading the failure **message** parses candidate-authored text — a candidate
  writes `raise Exception("AssertionError: forced")` on revert and is graded
  "strong" with zero behavioral content.
- Reading the failure **type** does not help either — a candidate writes
  `raise AssertionError()` directly: a genuine `AssertionError` by every
  mechanical check, still zero behavioral content.

When the candidate supplies the code that runs its own tests, you can prove a
test **flips** (necessary) but never that it **means** something (sufficient).
So the primitive makes only the claim it can defend — the flip — and hands the
meaning judgment to the layer that can make it (the two-layer design: mechanical
advisory signal + de-correlated evaluator authenticity).

## The mechanic

1. `changed = diff(base, candidate)`, split by `is_test_path` into **non-test**
   files (the change under test) and **test** files (supplied tests).
2. **Tree A** = the candidate tree, verbatim.
   **Tree B** = the candidate tree with the *non-test* change reverted to base
   content, tests kept exactly as supplied (so a flip stays observable).
3. Run the supplied test target against both trees. A test *flips* iff the
   harness observes it PASS on A and NOT pass on B (fail / error / vanish behind
   a collection error). `discriminates` iff at least one supplied test flips.

Both observations are made by the harness (we run pytest and read its junit); the
candidate supplies test *code*, never the pass/fail verdict, so the flip itself
is sound. Only the outcome **category** is read — never the candidate-authored
failure message or type.

Output shape (`DiscriminateResult.to_dict()`):

```json
{
  "discriminates": true,
  "necessary_not_sufficient": true,
  "tests_flipping": [{"test_id": "test_app::test_greet",
                       "outcome_candidate": "passed",
                       "outcome_reverted": "failed"}],
  "tests_total": 1,
  "notes": ["discriminates_true_is_necessary_not_sufficient__evaluator_must_read_flipping_test_source"],
  "changed_non_test_files": ["app.py"], "changed_test_files": ["test_app.py"]
}
```

`outcome_reverted` (`failed`/`error`/`collection_error`) is a **descriptive
fact, not a quality ranking** — the primitive deliberately does not rank failure
vs error, because that ranking is candidate-spoofable and carries no meaning.
There is no `strong_flips`/`weak_flips` and no `kind` label.

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
- **Error != pass, ever.** A flip *requires* a host-observed `passed` on the
  candidate tree; an error/failure there can never be a flip (no
  masquerade-as-pass).
- **A flip is only necessary.** A flip whose on-revert outcome is a failure, an
  error, or a collection error all count equally. The primitive does NOT rank
  them — that ranking (assertion vs incidental exception) is candidate-spoofable
  and therefore meaningless as evidence.
- **Manufactured flips are honest but not "proof."** A zero-work candidate can
  force a flip (throwaway change file + a test that only couples to its
  existence, or that `raise`s a misleading exception on revert). Such a flip
  *registers* (`discriminates=True`, necessary signal) but the output labels it
  `necessary_not_sufficient` and carries no meaningful/assertion grade — the
  evaluator, reading the test source, is where it dies.
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
#   -> 15 passed, 1 skipped   (the skip is the live-Docker sandbox test)

# 2. The whole repo still green (re-count per pytest.ini: no lost tests):
python -m pytest -q
#   -> 579 passed, 50 skipped

# 3. (optional) live sandbox integration — needs Docker + the sandbox image:
AQ_RD_SANDBOX=1 python -m pytest \
  tests/test_close_loop_revert_discriminate.py::test_discriminate_under_sandbox_runner -q
```

## Red-first evidence

### The sound negative filter

Against a naive `discriminate()` that trusts a pass count, the filter's sound
direction is pinned by:

```
test_zero_real_tests_does_not_discriminate          (forged census -> False)
test_noop_tests_do_not_discriminate                 (pass both ways -> False)
test_change_only_in_test_files_does_not_discriminate(nothing to revert -> False)
test_error_on_candidate_never_masquerades_as_pass   (error on candidate is not a pass)
test_mixed_suite_reports_only_the_discriminating_test
```

### The reframe (the confirmed spoof + the deeper truth)

The prior fix classified a flip as "strong" iff the on-revert `<failure>` was an
`AssertionError` — spoofable by `raise Exception("AssertionError: forced")` and,
more deeply, by `raise AssertionError()` directly. `test_manufactured_flip_is_not_labeled_meaningful`
(parametrized over `message_spoof`, `bare_assertion_error`, `import_coupling`)
asserts each spoof **registers as a flip** yet the output carries **no**
`strong_flips`, **no** `assertion_failure`/`meaningful` label, **no** per-flip
`kind`, and states `necessary_not_sufficient: true`.

Behavioral proof it is genuinely red-first: for the `message_spoof` candidate,
the *previous* contract produced `strong_flips=1` and
`tests_flipping[0].kind="assertion_failure"` — three contract violations the new
test catches — while the reframed contract produces none. (The flip itself is
`discriminates=True` under both; the reframe changes the *labeling*, not the
necessary signal.)

Against the real implementation: `15 passed, 1 skipped`.

## Scope / status

Primitive + tests only. **Not** wired into the live gate; nothing armed.
`SandboxedPytestRunner` reuses the existing sandbox with no changes to its
guarantees.
