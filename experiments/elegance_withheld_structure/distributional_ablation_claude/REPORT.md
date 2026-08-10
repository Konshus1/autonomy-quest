# Claude second-model distributional ablation: incomplete run report

## Status

**This run is incomplete and is not a replication result.** The preregistered matrix contained 135 generations. Claude completed 68 and left 67 unfilled after the subscription returned HTTP 429 on both allowed attempts for every remaining cell:

> `You've hit your session limit · resets 8am (America/New_York)`

The frozen policy does not permit a third attempt or backfilling an unfilled sample. I therefore did not resume those cells after the reset, did not count them as failures, and did not annotate a partial sample. The unchanged annotation preparation gate correctly refused to proceed because all 135 artifacts were not present. No mechanism-presence rate and no cross-family conclusion can be reported from this run.

## Frozen design and model provenance

The substantive design was copied from `../distributional_ablation/plan.json`. Its source SHA-256 is `ae67fcab4bffbffd953fb0a0661adf5fcf5423dfe70999dd6c6f97a38bb8f340`. Only the engine and sampling provenance fields were changed for the Claude run. The Claude plan was frozen before generation at SHA-256 `58fec027310348a78b67bdc9c90936a8735b4d1e47bf3578cccf56c9fdb1813d`.

All nine user prompts were copied byte-for-byte from the published run before generation. `prompt_manifest.json` records their files, lengths, and SHA-256 values and has SHA-256 `436c651eaf097db781ac977a0927210c77a47189484c0426914f76ed29ec3ed1`:

| Case | DEFAULT | MATCHED | IRRELEVANT |
|---|---|---|---|
| B13 | `373f7aa714cdf1eab2bd301c3b725d631cfb2b8d53a19d5520ccb9c018b53920` | `cd86d0609541ea429a007f5ad952afbd4544d61b2f886e907ec0c833c8f4adaa` | `925172f13a0939895f663bec0e948958b6f773473bf893c6fa237133c314bf25` |
| C03 | `6ba78d148efa3b7e531aa7cdd7daebf855ed22e44f1c12299aaa4c0439527060` | `843d0bd48e36f8d800e30e0539ae52f1497d69af04dbd781740afc02d497e29a` | `064a0a2189e4f74ef447ee347663f1ca7f310558203eaff173ed60a25a5d0823` |
| C20 | `5da7525892cbfe83506b1b3c01cd7fe28f16aa9c7d6777e9d248940806483cb1` | `181ef019ea64dbf0f005057d66db9431328f1a186309b65b013ca5807356c177` | `b54e8419b9331c2b3391643d2c3eec1b077141a6d7762092866dbb37b238df13` |

Generation used Claude Code `2.1.226 (Claude Code)` with fresh temporary working directories, `claude -p`, `--output-format json`, the unchanged JSON schema through `--json-schema`, `--no-session-persistence`, no tools, and no seed. `ANTHROPIC_API_KEY` variables were removed from each child environment. The auth preflight recorded `authMethod=claude.ai`, `subscriptionType=max`, and no API-key source. All 68 successful envelopes resolved to `claude-fable-5` with distinct session IDs. No metered API was used.

The full Claude transport envelope, stderr, return code, extracted schema-valid response, model receipt, normalized code, and frozen-test output are retained per sample.

## Per-cell result

A dash in the mechanism column means **not scored**, not zero. Correctness is shown only when the cell was complete; for the partial C03 MATCHED cell, the parenthetical count is descriptive among filled outputs and is not a 15-sample cell rate.

| Case | Condition | Filled | Unfilled | Correctness | Strict dual-reviewer mechanism presence |
|---|---|---:|---:|---:|---:|
| B13 | DEFAULT | 15 | 0 | 6/15 | not scored |
| B13 | MATCHED | 15 | 0 | 6/15 | not scored |
| B13 | IRRELEVANT | 15 | 0 | 9/15 | not scored |
| C03 | DEFAULT | 15 | 0 | 14/15 | not scored |
| C03 | MATCHED | 8 | 7 | incomplete (7/8 filled passed) | not scored |
| C03 | IRRELEVANT | 0 | 15 | incomplete | not scored |
| C20 | DEFAULT | 0 | 15 | incomplete | not scored |
| C20 | MATCHED | 0 | 15 | incomplete | not scored |
| C20 | IRRELEVANT | 0 | 15 | incomplete | not scored |

Within the one fully generated case, B13, the irrelevant worked example was numerically better on correctness than DEFAULT: 9/15 versus 6/15, a +20 percentage-point difference. The preregistered-style one-sided Fisher comparison is `p=0.233046`. This is only a correctness observation. Without blinded annotations, it says nothing about the named mechanism.

Across all filled outputs, 42/68 passed their frozen executable test. The unfilled outputs are not included in that numerator or denominator.

## Annotation and claim ceiling

`run_annotations.py` was kept on the Codex dual-reviewer mechanism and invoked in prepare mode only after generation stopped. It refused partial annotation with `67 of 135 incomplete`, as required. No reviewer saw any output and no partial mechanism votes were created.

Consequently:

- this run neither reproduces nor refutes the published 0/15 mechanism finding;
- it cannot support a claim about two model families;
- it provides no basis for a universal claim;
- the complete B13 correctness difference is exploratory evidence only.

A completed Claude study would have contradicted the proposed cross-family replication if either reviewer-consensus mechanism presence were nonzero in any IRRELEVANT cell, or if the irrelevant-example correctness advantage failed to appear under the predeclared comparison being claimed. Conversely, agreement on this one additional Claude model would strengthen the result from one model family to two, not to all models.

## Audit and negative control

`verify_filled_distribution.py` passed all 68 filled artifacts and preserved the 67 cells as explicitly unfilled. It checks byte-frozen prompts, the exact response schema, response-to-code normalization, subscription/model/session receipts, manifest coverage, and recomputed frozen test return codes.

The negative control selected the formerly passing `B13/default/sample02`, appended a deliberate runtime error in an isolated copied fixture, and showed:

- original recorded test return code: 0;
- mutated frozen-test return code: 1;
- filled-generation verifier return code: 1;
- real artifact SHA unchanged.

The receipt is `negative_control_receipt.txt`. This shows that the executable-content detector catches a positive mutation. It does not substitute for the unavailable dual-reviewer mechanism negative control or a passing 135-cell completion verifier.

Machine-readable coverage is in `partial_analysis.json`. The generation, annotation-refusal, completion-refusal, and negative-control receipts are retained alongside it.
