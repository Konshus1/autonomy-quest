# DeepSeek V4 Flash cost-lever extension

## Verdict

**One of the three frozen Haiku existence triples transferred to DeepSeek V4 Flash under the strict stability gate.** Defect-to-insurance chain ladder passed: all ten DeepSeek baselines missed the load-bearing numeric projection, the surface arm missed it, the structural arm produced it, and the frozen Fable frontier produced it unaided. Chao1 failed because DeepSeek baseline succeeded in 8/10 blind-conservative evaluations. Robot-to-Engset failed because DeepSeek surface succeeded while DeepSeek structural gave the wrong result.

## Updated triple table

| case | Haiku holds | Haiku frontier/structural envelope | DeepSeek baseline core successes | stable baseline failure | DeepSeek surface | DeepSeek structural | DeepSeek holds | DeepSeek frontier/structural envelope |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `chao1` | yes | 41.28x | 8/10 | no | miss | hit | **no** | not earned |
| `defect_chain_ladder` | yes | 12.97x | 0/10 | yes | miss | hit | **yes** | 2423.25x |
| `robot_engset` | yes | 12.20x | 0/10 | yes | hit | miss | **no** | not earned |

The stability rule was frozen as zero baseline core successes in ten completed samples. A single success invalidates the leg. For the two 0/10 cases, the exact one-sided 95% upper bound on baseline success probability is still 25.89% (two-sided 95% interval 0–30.85%), so this is only a passed ten-draw screen, not proof of population-level reliability. All three sets had 10/10 distinct response hashes.

## Baseline resample distributions

- `chao1`: mechanical `1101000010`; blind Fable `1111101110`; blind Codex `1111101110`; conservative any-evaluator `1111101110` (8/10 successes).
- `defect_chain_ladder`: mechanical `0000000000`; blind Fable `0000000000`; blind Codex `0000000000`; conservative any-evaluator `0000000000` (0/10 successes).
- `robot_engset`: mechanical `0000000000`; blind Fable `0000000000`; blind Codex `0000000000`; conservative any-evaluator `0000000000` (0/10 successes).

For Chao1, the old deterministic token matcher recognized only 4/10 successes, while both blind judges recognized 8/10. The conservative union is reported, preventing a brittle regex from manufacturing baseline stability.

## Blind primary-arm scores

| case | judge | baseline sample 1 | surface | structural | frontier |
|---|---|---:|---:|---:|---:|
| `chao1` | fable | 4 | 2 | 5 | 5 |
| `chao1` | codex | 4 | 2 | 5 | 5 |
| `defect_chain_ladder` | fable | 1 | 1 | 5 | 4 |
| `defect_chain_ladder` | codex | 1 | 1 | 5 | 5 |
| `robot_engset` | fable | 3 | 5 | 3 | 5 |
| `robot_engset` | codex | 3 | 5 | 3 | 5 |

Each judge actually scored all 13 opaque candidates: ten baseline resamples, one surface, one structural, and the frozen frontier. Candidate maps and order were independently randomized. Fable used `claude-fable-5`; the Codex v0.144.0 envelope identifies its canonical model as `gpt-5.4` (the requested `gpt-5.4-codex` label resolves to that model in this CLI). One Fable Engset judgment omitted a candidate and was retained as invalid; a fresh same-prompt judgment supplied the complete 13-candidate result. The offline verifier binds each normalized judgment back to its raw CLI envelope, model, and session.

The strict 0/10 threshold and broad leg gate were preregistered. The exact conservative reconciliation was formalized after planner outputs, so it is disclosed rather than presented as preregistered: baseline/surface count any success found by the mechanical validator or either blind judge, while structural/frontier require corroboration. Chain-ladder load-bearing success intentionally uses numeric rubric criteria 2–4 plus no fatal error, preserving the legacy numeric-core estimand; criterion 1 is corroborative. This post-run reconciliation changes no verdict relative to the legacy deterministic gate: Chao already had 4/10 mechanical hits, chain remains the sole triple, and Engset surface hits while structural misses.

## Cost

DeepSeek valid planner calls reported **$0.008865226**. Excluded stdin-transport calls reported **$0.008925090**. Total CLI-attributed metered cost was **$0.017790315**, well below the $3 cap. Two excluded incomplete stdin attempts emitted no final usage record, so any provider-side charge for those calls is unknown and not included. Fable and Codex judges used subscriptions and added $0 metered spend.

The one earned DeepSeek ratio is **2423.25x**: the frozen Fable price-equivalent frontier envelope ($0.412996) divided by the valid DeepSeek structural provider cost ($0.0001704304). This heterogeneous CLI/provider envelope is diagnostic, not a generic model price ratio. DeepSeek structural was 186.77x below the Haiku structural CLI price-equivalent on this case, much larger than the expected ~7x, mainly because provider pricing and interface/system-token accounting differ.

## Prompt and provenance checks

The copied planner prompts are byte-identical to the already-frozen prompt files, including the no-final-newline Chao prompts and the trailing-newline multi-case prompts. The verifier reads the emitted user event from every raw Prime Agent JSONL stream and requires its SHA to equal the frozen file SHA. Baseline and frontier prompts remain byte-identical.

An initial stdin-based run was excluded after discovering Prime Agent 0.7.1 trims piped input. It is frozen under `invalid_stdin_*` and charged to the spend ledger. The valid v2 protocol passes the complete prompt, including its trailing newline, as one argv item after `--`; `ea2cd8a` froze that correction before valid calls. Exact user-message equivalence is earned. Full-request equivalence is not: Prime Agent uses a different system prompt and provider stack from Claude CLI.

## Reproduce

```bash
uv run python experiments/graph_analogy_cost_lever/run_deepseek_extension.py \
  --output experiments/graph_analogy_cost_lever/deepseek_extension/receipt.json
uv run pytest tests/test_structural_analogy_cost_lever.py -q
```

The offline verifier checks all 36 valid planner sessions, exact emitted prompt bytes, compressed raw streams, response and receipt hashes, provider/model IDs, unique sessions, costs, resample vectors and exact intervals, both blind judges, and the frozen matcher selections.

## Honest aggregate

> One DeepSeek V4 Flash triple across three previously positive Haiku cases. The lever transferred for chain ladder, was unstable for Chao1 because baseline often succeeded, and reversed for Engset because surface succeeded while structural failed. Existence-only and conditional on these hand-constructed candidate libraries and one surface/structural draw per case.
