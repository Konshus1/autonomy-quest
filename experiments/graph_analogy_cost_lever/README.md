# Cheap-model structural analogy cost-lever experiment

This is the cost-lever continuation of graph-constrained analogy task #4834. It asks a narrower,
economic question: can a cheap planner plus a structurally retrieved source produce a valid plan
element that the cheap planner alone and with a lexical source omit, while a frontier planner
produces it without help?

## Verdict

**One existence triple passed.** On a frozen fuzz-testing target, Haiku 4.5 had to estimate unseen
failure signatures from 40 singleton signatures and 10 doubletons.

| arm | retrieved context | Chao1 + 80 unseen + 180 total |
|---|---|---|
| Haiku baseline | none | no |
| Haiku surface | same-domain coverage-gate case | no |
| Haiku structural | cross-domain rare-species graph | yes |
| Fable 5 frontier | none | yes |

The structurally scaffolded Haiku instantiated `40^2/(2*10) = 80` unseen classes and about 180
total. Baseline named broad Good-Turing/Zipf fitting but never selected or instantiated Chao1.
The lexical arm remained qualitative. Fable 5 produced the same quantitative core unaided.

A retrieval-blinded Fable judge saw only opaque labels and scored the five-item rubric:

```text
frontier 5/5, structural Haiku 4/5, baseline Haiku 2/5, surface Haiku 1/5
```

Structural Haiku lost one point because it framed rather than explicitly made the no-release
decision. The justified claim is therefore parity on the **load-bearing quantitative plan element**,
not full answer parity.

## Command that earns it

```bash
uv run python experiments/graph_analogy_cost_lever/run_experiment.py \
  --output experiments/graph_analogy_cost_lever/receipt.json
uv run pytest \
  tests/test_structural_analogy_discrimination.py \
  tests/test_structural_analogy.py \
  tests/test_structural_analogy_extracted.py \
  tests/test_structural_analogy_cost_lever.py -q
```

Expected compact output:

```text
verdict: cost-lever-existence-proof
core element baseline/surface/structural/frontier: False False True True
retrieval surface/structural: protocol_coverage_gate ecology_chao1
matcher true/near scores: 1.52666667 1.41666667
controls scrambled/opaque/partial: False True True
blind scores baseline/surface/structural/frontier: 2 1 4 5
```

## Relation match and stress controls

The target graph is a three-relation system:

```text
campaign --sampling--> observed signatures
observed --singleton_frequency--> signatures seen once
observed --doubleton_frequency--> signatures seen twice
```

The selected ecological source preserves all three roles and relations, while carrying two extra
noise relations. Its transfer graph introduces a Chao1 estimator fed by the singleton and
doubleton roles. Surface retrieval instead selects a protocol-coverage source whose graph has no
correspondence.

The matcher was hardened in two ways:

1. It can map the smaller role set into a subset of a richer source graph, rather than rejecting
   any source with more roles than the target.
2. Its score weights target explanation and source coverage separately:

```text
0.35 * source_coverage
+ 0.65 * target_coverage
+ 0.50 * connected_fraction
+ 0.25 * depth_fraction
```

Acceptance requires at least two connected correspondences and at least 40% coverage in each
direction. On frozen controls:

- exact topology with two source-noise edges: accepted, score `1.52666667`;
- plausible near miss sharing two of three relations: accepted but lower, `1.41666667`;
- scrambled topology with the same family multiset: rejected;
- all source roles, predicates, and surface labels opaquely renamed: accepted at the exact same
  score;
- partial two-relation target: true source remains accepted;
- one extra target noise relation: true source remains above the near miss;
- heavy source noise (three core matches among eight edges): rejected at the 40% coverage guard.

The principled break is also frozen: if the target extractor emits only the two relations shared
by the true source and near miss, the near miss becomes observationally indistinguishable and can
score higher because it is less noisy. No topology-only matcher can recover a relation the target
graph omitted. Extraction completeness or explicit ambiguity handling is required there.

## Cost and protocol

All experiment calls removed `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`; the frozen auth receipt
shows `authMethod: claude.ai`, `subscriptionType: max`. Metered experiment spend was $0. The CLI
envelopes still report price-equivalent estimates:

| arm | estimate |
|---|---:|
| Haiku baseline | $0.01306 |
| Haiku structural | $0.02432 |
| Fable frontier | $1.00386 |

That is **41.28x**, not 100x, for the realized structural-versus-frontier envelopes. This result
supports a cost lever but does not earn the stronger 100x empirical cost claim. One pre-experiment
CLI smoke check accidentally inherited an API key and reported $0.01572; it was not an experiment
arm, and all subsequent calls used verified subscription auth.

The final arm prompts, SHA-256 hashes, raw CLI envelopes, model responses, anonymous judge prompt,
opaque-label map, graphs, and deterministic receipt are frozen in this directory. Before selecting
this case, five Haiku baseline probes were run and retained under `prospective/`; Chao1 was the
clean baseline-absence case promoted. This selection makes the result an existence proof, not an
unbiased success-rate estimate.

## Falsifier and limits

The triple would have failed if baseline or lexical Haiku had computed 80 unseen and 180 total,
if structural Haiku had omitted or miscomputed them, if frontier Fable had omitted them, or if the
topology/rename/noise controls had selected the wrong source. It did not test downstream software
release outcomes, repeated-run reliability, a multi-task benchmark, or live AQ integration. No
merge or live-loop wiring was performed.
