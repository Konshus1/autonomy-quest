# Claude second-model distributional ablation: final report

## Question and frozen inputs

This is the second-model replication of `../distributional_ablation/`. It asks whether an irrelevant worked example improves correctness while transferring none of the three frozen named mechanisms, and whether a matched source uniquely induces any mechanism.

The source Codex plan SHA-256 is `ae67fcab4bffbffd953fb0a0661adf5fcf5423dfe70999dd6c6f97a38bb8f340`. The Claude plan was frozen before generation at `58fec027310348a78b67bdc9c90936a8735b4d1e47bf3578cccf56c9fdb1813d`. All nine user prompts were copied byte-for-byte from the source run before generation. Their unchanged `prompt_manifest.json` SHA-256 is `436c651eaf097db781ac977a0927210c77a47189484c0426914f76ed29ec3ed1`.

The initial subscription window filled 68/135 cells and then returned HTTP 429. After the operator explicitly authorized continuation following the 08:00 America/New_York reset, the remaining 67 cells were generated from the same prompt files and schema. Attempts 1-2 and the authorization receipt remain on disk; continuation attempts are numbered 3-4. No cell was replaced and no schema-valid response was regenerated because it failed a test.

Generation used Claude Code 2.1.226 through `claude -p`, fresh temporary working directories, `--output-format json`, the unchanged schema via `--json-schema`, `--no-session-persistence`, safe mode, and no tools or seed. API-key environment variables were removed. All 135 successful envelopes resolve to `claude-fable-5`, with 135 distinct session IDs. Both phases authenticated as claude.ai Max rather than an API key. The CLI auth receipt reported different account/org metadata after the reset; this is retained as a provenance limitation.

## Blinding and scoring

All 135 outputs existed before reviewer preparation. The same secret-salt opaque labeling, condition blinding, candidate material, frozen rubrics, two independent ephemeral Codex reviewers, citation validation, and strict reviewer-A AND reviewer-B consensus rule were used. Generated `mechanisms` and `trace` claims were excluded from reviewer packets.

Fable produced longer code than the Codex run. The unchanged nine-candidate packet construction exceeded the annotation runner's 120 KB operational guard (`max=147,976` bytes). Before any reviewer call, only that byte ceiling was increased to 200 KB. Packet composition, prompts, schema, rubrics, reviewer independence, and consensus were unchanged. The receipt is `annotation_packet_limit_receipt.txt`.

The source analyzer also had a latent narrative-only bug: when no case qualified, it said 0/135 mechanisms even if decoded positives existed. The Claude outcome triggered it. The interpretation branch was corrected without changing rates, decoding, gates, Fisher/Wilson calculations, or the primary outcome. The receipt is `analysis_interpretation_fix_receipt.txt`.

## Per-cell result

| Case | Condition | Correctness | Strict dual-reviewer mechanism presence |
|---|---|---:|---:|
| B13 | DEFAULT | 6/15 | 0/15 |
| B13 | MATCHED | 6/15 | 0/15 |
| B13 | IRRELEVANT | 9/15 | 0/15 |
| C03 | DEFAULT | 14/15 | 5/15 |
| C03 | MATCHED | 13/15 | 1/15 |
| C03 | IRRELEVANT | 15/15 | 4/15 |
| C20 | DEFAULT | 15/15 | 0/15 |
| C20 | MATCHED | 15/15 | 0/15 |
| C20 | IRRELEVANT | 15/15 | 0/15 |

Overall, 108/135 outputs passed the frozen executable tests.

### Correctness

Aggregated descriptively across the three cases:

- DEFAULT: 35/45
- MATCHED: 34/45
- IRRELEVANT: 39/45

IRRELEVANT improved over DEFAULT by 4/45, or 8.9 percentage points, the same aggregate count difference as the published Codex run. The one-sided Fisher comparison is `p=0.204448`. By case, the difference was +3/15 on B13 (`9/15` versus `6/15`, `p=0.233046`), +1/15 on C03 (`15/15` versus `14/15`, `p=0.5`), and zero on C20. The correctness direction therefore reproduces numerically, but the individual comparisons are descriptive and not strong evidence by themselves.

### Mechanism presence

The published all-zero mechanism result does **not** reproduce. C03 has ten strict-consensus positives:

- DEFAULT: samples 1, 5, 8, 13, 15
- MATCHED: sample 1
- IRRELEVANT: samples 3, 9, 12, 15

Both reviewers cited executable shared traversal structure. For example, C03 IRRELEVANT sample 3 cites `Workspace._walk` plus `Workspace.total_word_count` and `Workspace.unresolved_markers`; samples 9 and 15 cite `Workspace._iter_items`; sample 12 cites `Workspace._walk`. In each, one shared dispatcher covers leaves/groups and at least two operations consume it. These are content-based positives, not generated self-reports or names alone.

The one-sided mechanism comparisons MATCHED > DEFAULT and MATCHED > IRRELEVANT are `p=0.991571` and `p=0.978927`, respectively. The observed ordering is the reverse of matched-source specificity: DEFAULT 5, IRRELEVANT 4, MATCHED 1. Under the frozen plan, any IRRELEVANT consensus positive classifies the case as an example-quality/structural confound. C03 therefore fails the irrelevant-control gate, and its generic cross-condition presence does not identify analogy transfer.

B13 and C20 retain 0/15 in all three mechanism cells, but neither supplies matched-specific existence. No case qualifies. Machine-readable outcome: `primary_outcome=null_no_case_qualifies`, `irrelevant_control_verdict=named_mechanism_present_example_quality_confound`.

## Reviewer sensitivity

| Case | Condition | Reviewer A YES | Reviewer B YES | Consensus |
|---|---|---:|---:|---:|
| B13 | DEFAULT | 1/15 | 0/15 | 0/15 |
| B13 | MATCHED | 0/15 | 0/15 | 0/15 |
| B13 | IRRELEVANT | 1/15 | 0/15 | 0/15 |
| C03 | DEFAULT | 9/15 | 6/15 | 5/15 |
| C03 | MATCHED | 2/15 | 2/15 | 1/15 |
| C03 | IRRELEVANT | 8/15 | 5/15 | 4/15 |
| C20 | DEFAULT | 0/15 | 0/15 | 0/15 |
| C20 | MATCHED | 0/15 | 0/15 | 0/15 |
| C20 | IRRELEVANT | 0/15 | 0/15 | 0/15 |

The reviewers differ in sensitivity, but the strict AND positives are not driven by a single reviewer.

## Replication judgment and claim ceiling

My predeclared falsification criterion was that any strict-consensus named mechanism in an IRRELEVANT cell would contradict the cross-family reproduction of the published all-zero result. C03 IRRELEVANT is 4/15, so that criterion was met: **the specific 0/15-in-all-nine-cells mechanism number is model-dependent and failed to replicate.**

The broader interpretation did replicate and became more diagnostic. On both models, the correctness direction favored the irrelevant worked example, while MATCHED did not uniquely induce the named mechanism. On Fable 5, C03's ordering was DEFAULT 5, IRRELEVANT 4, MATCHED 1: the condition that should show transfer had the fewest positives. This is evidence against matched-source analogical transfer and evidence that the mechanism detector/rubric can be triggered by generic shared-dispatcher solutions. The mixed headline is therefore: the correctness direction recurred across model families; the all-zero mechanism count did not; the not-transfer interpretation held on both.

This is a genuine cross-family result across two **capable, large models**: GPT-5.6 Sol and Claude Fable 5. Fable 5 is a large, slow, intelligent model, not a small/fast or weak model, so its tier does not discount or disqualify the comparison. The evidence now spans two capable models of different families.

The ceiling remains two models, not all models. The Claude side specifically used the CLI-resolved `claude-fable-5` configuration and Claude Code system layer; the default model was not pinned by name before generation. The two-phase subscription reset and changed auth account/org metadata are provenance limitations, not capability limitations. The result does not establish universality across every Anthropic model, every OpenAI model, model families generally, or future defaults.

## Verification and negative control

`verify_distribution.py` passes the complete study:

```text
PASS: distributional ablation completion gate
cells=135 generations=135 correct=108 reviewers=2 consensus=135
```

The verifier checks the frozen plan/contexts/contracts/tests, unchanged prompt bytes and hashes, all Claude envelopes and distinct sessions, response normalization, all 135 recomputed tests, opaque labels and balanced blinded packets, exact packet reconstruction, two independent Codex receipts, citations, consensus, and decoded analysis.

`run_negative_control.py` was rerun after all 135 generations and reviews existed. It copied the complete study, deliberately broke the formerly passing `B13/default/sample02`, confirmed the mutated frozen test failed, and required the full completion verifier to reject response/code hashes, test consistency, and both blinded packet snapshots. The real artifact SHA remained unchanged. Receipt: `negative_control_receipt.txt`.
