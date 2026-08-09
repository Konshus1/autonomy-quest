# Track C — Structural Analogy Search Measurement (EXPERIMENTAL)

> **UNWIRED / ADVISORY / NO AUTOMATIC PROMOTION.** This directory is an
> offline experiment. It is not imported by the AQ loop, is not a prerequisite
> for any other track, and cannot promote a principle to authoritative status.

## Question and estimand

Does an **explicit cross-domain structural-analogy pipeline** produce more
useful candidate actions than (a) same-model direct ideation and (b) same-model
semantic case retrieval on the same problems? The experiment cannot establish
that the direct model used no latent analogy internally. A numeric zero or
negative delta is a completed result.

## Frozen protocol (before model generation)

- Generator and judge: `deepseek-v4-flash`, invoked only by this explicit CLI
  using `DEEPSEEK_API_KEY`; no loop/runtime code makes an API call.
- Experimental unit: problem, N=8 heterogeneous and N=8 homogeneous Watchdog.
- Three isolated one-call generation arms, maximum three candidates each:
  `direct`, `semantic`, `structural`.
- Semantic baseline: the same model sees source-case titles/summaries, selects
  by topic/wording/task meaning, and proposes candidates in one call. It gets
  the whole library, which is a conservative context advantage over the
  structurally retrieved single case. It is not an embedding index; this is a
  stated limitation rather than silently calling keyword overlap “semantic.”
- Structural retrieval scores only topology-preserving correspondences among
  **observed** directed relation families. Source intervention/transfer edges
  are excluded from retrieval scoring. The retrieved source must have a
  different domain on the heterogeneous corpus.
- A valid structural record needs at least two explicit role correspondences,
  two explicit directed relation correspondences, and one provenance-linked
  transferred candidate inference.
- Three judge calls per problem. Each sees the target and candidate prose only,
  never mappings, provenance, or arm names. Arm labels/order are deterministically
  shuffled. Usefulness is an anchored integer 1–5. Problem-level judge means
  are the independent observations.
- Primary numbers: paired mean usefulness deltas `structural-direct` and
  `structural-semantic`, 10,000-sample problem bootstrap 95% CIs, and exact
  one-sided paired sign-flip p-values. Holm adjusts the two comparisons.
- Positive evidence requires both adjusted p<.05 and lower bootstrap CI>0.
  Otherwise the conclusion is the honest `measured_null_or_negative`.
- The same executable pipeline and model run the homogeneous Model Catalog
  Watchdog corpus. Any statistically significant positive gain against either
  baseline voids a heterogeneous positive result. Non-significance is **not**
  claimed as equivalence; N=8 is underpowered for equivalence.

The corpora are curated and human-authored. Target graphs contain observed
problem dynamics but no proposed intervention. The test therefore measures
retrieval/mapping/transfer **given** a structural representation, not automatic
schema extraction and not live mission outcome improvement.

## Run

```bash
python3 -m pytest experiments/structural_analogy/test_experiment.py -q
DEEPSEEK_API_KEY=... python3 experiments/structural_analogy/experiment.py \
  --output experiments/structural_analogy/results.json
python3 experiments/structural_analogy/verify_result.py \
  experiments/structural_analogy/results.json
```

The completion verifier is intentionally falsifiable. Before citing it, copy
`results.json`, delete one record's `transferred_candidate_inferences`, and
confirm the verifier exits nonzero. Restore the real artifact and confirm it
passes. A verifier that passes the broken artifact is not evidence.

## What this does not do

- no import or wiring into `runner/`, management, gateway, executor, or planner;
- no dependency for Tracks A, B, E, F, or G;
- no database writes or live-loop calls;
- no claim that existing `#4904` is resolved or superseded;
- no automatic application or authoritative promotion (DR12 unchanged);
- no reuse of T3 token-overlap clustering or T7's supplied action/mock outcome.
