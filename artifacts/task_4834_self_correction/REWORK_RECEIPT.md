# Task 4834 self-correction rework receipt

Round 3 closes the final dotted-semver under-merge while preserving the
round-2 bare-numeric boundary, and casefolds the remaining within-sibling
classification comparison.

## Round-3 closure

- F2 dotted semver: version suffixes accept repeated dotted numbers after an
  explicit `v`, `rev`, or `version` token. `rule-x.v2.1` and `rule-x.v2.3`
  therefore normalize to `rule-x` and enumerate as siblings.
- Bare-numeric boundary: the explicit token remains mandatory. `policy-1` /
  `policy-2`, `gdpr-art-5` / `gdpr-art-9`, and `checklist-7` / `checklist-8`
  remain different identities. `ruleV2` remains unchanged because it has no
  separator before the version token.
- Label consistency: the within-sibling validated/current comparison now
  casefolds both sides.

The single parametrized regression matrix exercises both input directions
through `normalize_rule_identity`, `sibling_hypotheses`, and `consult`:

| Case | Expected | Result |
| --- | --- | --- |
| `rule-x.v2` / `rule-x.v3` | merge | PASS |
| `rule-x.v2.1` / `rule-x.v2.3` | merge | PASS |
| `rule-x.v2.1.5` / `rule-x.v2.9` | merge | PASS |
| `RULE-X.V1` / `rule-x.v3` | merge | PASS |
| `rule.rev3` / `rule.rev4` | merge | PASS |
| `policy-1` / `policy-2` | do not merge | PASS |
| `gdpr-art-5` / `gdpr-art-9` | do not merge | PASS |
| `checklist-7` / `checklist-8` | do not merge | PASS |
| `ruleV2` / `ruleV3` | do not merge; `ruleV2` unchanged | PASS |

## Round-3 verification

Run from the task worktree:

```console
$ pytest -q tests/test_self_correction_consultant.py
................................                                         [100%]
32 passed in 0.05s

$ python scripts/prove_self_correction_controls.py
# exit 0; all A1-A8 and R2 deterministic controls passed
```

Round 2 closes all three closure-review findings. F3 retains option (a), now with a behaviorally necessary persisted learned type rather than an output-equivalent fixture.

## Round-2 red-first evidence

The round-2 controls were committed in `e7c03b5` directly on the requested `e471110` implementation, before either production fix.

```console
$ git checkout e7c03b5
$ pytest -q tests/test_self_correction_consultant.py -k 'r2_bare_numeric or r2_case_variant or r2_prior'
FF.

FAILED tests/test_self_correction_consultant.py::test_r2_bare_numeric_rule_ids_are_not_versions_but_explicit_versions_are
FAILED tests/test_self_correction_consultant.py::test_r2_case_variant_overturned_label_cannot_hide_behind_stale_validation
2 failed, 1 passed, 20 deselected in 0.05s
```

The first failure enumerated `policy-2` as a sibling of `policy-1`. The second returned `ConsultantPass(... all siblings validated, no change)` for stale `Safe` evidence after `safe` was overturned. The passing F3 control shows the prior-type fixture itself did not depend on the two production fixes.

The earlier A1-A8 adversarial tests remain preserved in `b7b4fb8` (parent implementation `4a32c4b`) and are documented in `red_rework_a1_a8.txt`.

## Round-2 closure

- F2: normalization now strips only explicit `v`, `rev`, or `version` suffix tokens. `policy-1` and `policy-2` remain distinct; `rule-x.v1`, `rule-x.v2`, and case variants share an identity.
- F1: the overturned-label comparison casefolds both classifications, consistent with `ReusableLearningType.fires_on`.
- F3: the acceptance fixture supplies `persisted.custom_review.v7`, whose ID and `FRAME_REVIEW` response differ from the derived type. The same snapshot returns `SIBLING_AUDIT` without it and `FRAME_REVIEW` with it, proving that prior learned state changes `consult()` output end to end.

## Green commands

Run from the task worktree at the final round-2 commit:

```console
pytest -q tests/test_self_correction_consultant.py -k 'r2_bare_numeric or r2_case_variant or r2_prior'
pytest -q tests/test_self_correction_consultant.py tests/test_meta_mode.py
python scripts/prove_self_correction_controls.py
pytest -q
```

The worker self-review uses absolute test paths because the shared reviewer launcher executes parallel tests from its own repository:

```console
env PYTHONPATH="$PWD" pytest -q "$PWD/tests/test_self_correction_consultant.py" "$PWD/tests/test_meta_mode.py"
```

The deterministic proof prints the packet committed as `acceptance_receipt.json`. No merge to `main` is performed by this receipt.
