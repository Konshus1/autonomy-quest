# Task 4834 self-correction rework receipt

F1, F2, and F3 use option (a): meta-learning is content-driven and `consult()` is its production consumer.

## Red-first evidence

The adversarial tests were committed before production changes in `b7b4fb8` (parent/current implementation `4a32c4b`).

```console
$ git checkout b7b4fb8
$ pytest -q tests/test_self_correction_consultant.py
FFFFFFFFFF..FFFFFFFF
18 failed, 2 passed in 0.14s
```

Each named adversarial control A1-A8 failed on that red commit. The failures showed the old types did not accept `corrected_at`, `validated_at`, semantic rule-family data, enriched reference-event content, or learned types for a production consumer. This is the pre-fix proof, not a mutation of the finished implementation.

## Green commands

Run from the task worktree:

```console
pytest -q tests/test_self_correction_consultant.py tests/test_meta_mode.py
python scripts/prove_self_correction_controls.py
pytest -q
```

The deterministic proof prints the same packet committed as `acceptance_receipt.json`. In particular:

- A1/A2: stale or unordered agreeing validation cannot vouch for the overturned label.
- A3/A4: strictly newer validation and siblings not carrying the overturned label remain real PASS controls.
- A5/A6: case/version variants and explicitly declared rename/copy families enumerate together.
- A7: disagreement in the corrected item's own record fails closed as `class unknown / cannot enumerate siblings`.
- A8: rule/old/new content produces distinct learned types, and `consult()` consumes a prior matching type end-to-end.

No merge to `main` is performed by this receipt.
