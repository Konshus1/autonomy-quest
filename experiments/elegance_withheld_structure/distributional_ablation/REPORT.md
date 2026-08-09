# Distributional elegance ablation: final report

## Question and preregistration

This successor experiment tests an **existence claim**, not an average-win claim: does a frozen, source-traceable organizing mechanism occur in any of 15 independent MATCHED-source samples while occurring in none of 15 DEFAULT samples and none of 15 correct-but-structurally-irrelevant worked-example samples?

The design was frozen before generation in `plan.json` (SHA-256 `ae67fcab4bffbffd953fb0a0661adf5fcf5423dfe70999dd6c6f97a38bb8f340`). It replaced an invalid deterministic same-seed comparison after the engine accepted a seed but produced 0/9 identical response pairs. The three cases and mechanisms were:

- B13: `M_B13_REFERENCE_AWARE_GLOBAL_REWRITE`
- C03: `M_C03_UNIFORM_RECURSIVE_CONTENT_NODE`
- C20: `M_C20_MODEL_VIEW_ACTION_INTERPRETER_TRIAD`

There were 3 cases × 3 conditions × 15 independent ephemeral `gpt-5.6-sol` Codex-subscription invocations = 135 generations. Transport/schema replacement attempts were allowed, but no prompt-changing retries or seed claims were made.

## Blinding and scoring

All 135 artifacts existed before annotation. A secret salt assigned SHA-256 opaque labels. Each reviewer received the public contract, exact frozen mechanism rubric, generated implementation plan for context, and numbered code, but no condition, sample index, prompt, generation trace, or filesystem path. Generated self-reported mechanisms and traces were excluded.

Two independent ephemeral reviewers used independently mixed nine-candidate batches. A YES was effective only if every required evidence item and the frozen source relation had concrete, mechanically validated code-symbol, exact-quote, and line citations. Consensus was the preregistered conservative AND of both reviewers. Disagreements are not positives.

Executable correctness was recomputed with the frozen tests and takes priority over structure or quality.

## Primary result

| Case | DEFAULT mechanism | MATCHED mechanism | IRRELEVANT mechanism | DEFAULT correct | MATCHED correct | IRRELEVANT correct |
|---|---:|---:|---:|---:|---:|---:|
| B13 | 0/15 | 0/15 | 0/15 | 11/15 | 11/15 | 15/15 |
| C03 | 0/15 | 0/15 | 0/15 | 14/15 | 13/15 | 14/15 |
| C20 | 0/15 | 0/15 | 0/15 | 15/15 | 15/15 | 15/15 |

For every mechanism cell, the 95% Wilson interval is `[0.0000, 0.2039]`. For every case, the one-sided Fisher exact comparisons MATCHED > DEFAULT and MATCHED > IRRELEVANT both have `p=1.0`. These statistics are descriptive; no significance threshold substitutes for the frozen existence gate.

No case has a correct consensus-positive MATCHED candidate. Source traceability and the not-worse selector therefore cannot be satisfied, and the maintainability comparison is not evaluable rather than silently waived.

**Primary outcome: `null_no_case_qualifies`; qualifying cases: `[]`.** This is a strict distributional existence null for these mechanisms, tasks, prompts, model, and N. It does not establish equivalence or a universal inability to transfer analogies.

## Correct-but-irrelevant control

The irrelevant worked example did not produce any frozen named mechanism: 0/15 in all three cases. It therefore does not create an example-quality/structural-mechanism confound in this run.

It did, however, improve executable correctness on B13: 15/15 versus DEFAULT 11/15, a +26.7 percentage-point difference. The exploratory one-sided Fisher comparison IRRELEVANT > DEFAULT is `p=0.0498084` (two-sided `p=0.0996169`). C03 tied DEFAULT at 14/15 (`p=0.758621` one-sided), and C20 tied at 15/15 (`p=1.0`). Aggregated descriptively across heterogeneous cases, IRRELEVANT was 44/45 and DEFAULT was 40/45 (`p=0.101386` one-sided).

So the precise answer to “does the correct irrelevant control beat DIRECT/default?” is: **on correctness, yes numerically for B13 and in the pooled count, but not for C03 or C20; on the named organizing mechanism, no in every case.** The B13 result is compatible with generic worked-example or prompt-context help, not evidence for structural analogy transfer.

## Reviewer asymmetry and limitation

The individual effective-YES counts were:

| Case | Condition | Reviewer A | Reviewer B | Consensus |
|---|---|---:|---:|---:|
| B13 | DEFAULT | 1/15 | 0/15 | 0/15 |
| B13 | MATCHED | 0/15 | 0/15 | 0/15 |
| B13 | IRRELEVANT | 0/15 | 0/15 | 0/15 |
| C03 | DEFAULT | 5/15 | 0/15 | 0/15 |
| C03 | MATCHED | 6/15 | 0/15 | 0/15 |
| C03 | IRRELEVANT | 4/15 | 0/15 | 0/15 |
| C20 | DEFAULT | 0/15 | 0/15 | 0/15 |
| C20 | MATCHED | 0/15 | 0/15 | 0/15 |
| C20 | IRRELEVANT | 0/15 | 0/15 | 0/15 |

Reviewer B was uniformly conservative, while reviewer A saw C03-like structure at similar rates in all three conditions. The frozen strict-consensus rule requires the reported zeros, but this asymmetry is a real sensitivity/power limitation. It also means the lone-reviewer C03 pattern, even if considered informally, does not isolate MATCHED from DEFAULT or the irrelevant control.

## Audit and falsification

`verify_distribution.py` independently recomputes:

- hard-coded preregistration, context, task-contract, executable-test, distribution-runner, annotation-runner, analyzer, annotation-manifest, and response-schema hashes;
- exact 135-cell coverage and condition-specific prompts;
- raw-response normalization, plan/trace, code, response, and prompt hashes;
- all 135 pinned executable tests;
- 135 distinct generation and 30 distinct reviewer session receipts whose stdout exactly reproduces the frozen response and whose stderr records `gpt-5.6-sol`/OpenAI;
- opaque labels from the secret salt, balanced independent reviewer packets, and byte-exact packet reconstruction from the pinned template and allowed fields;
- nonempty exact quotes, code-overlapping structural symbols/lines, effective votes, strict consensus, and decoded analysis.

The verifier passes the real study. `run_negative_control.py` copies the study, deliberately breaks a formerly passing B13 solution, confirms its frozen test fails, and requires the completion verifier to reject the fixture. The exact receipt is in `negative_control_receipt.txt`.

Machine-readable rates, Wilson intervals, Fisher values, gates, disagreement counts, and verdicts are in `analysis.json`. Raw generations, tests, prompts, reviewer responses, validated citations, consensus, and the restricted decode map are retained under `results/` and `annotation_results/`.
