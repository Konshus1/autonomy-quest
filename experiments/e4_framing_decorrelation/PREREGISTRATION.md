# E4 pre-registration — multi-framing decorrelation (task #4834)

**Committed BEFORE any model call.** Ground truths verified by simulation
(`verify_answers.py`, all 12 PASS). Judge-free: every problem has an exact
numeric answer; scoring is `|ans - truth| <= max(0.005, 0.01*|truth|)`.

## Hypothesis under test (Kevin's mental-models thesis, claim 4)

Multiple *different* structural framings of a problem, reconciled, catch errors
that mere resampling (same framing, temperature diversity) does not — because
framing diversity decorrelates errors.

## Design

Model: `claude-haiku-4-5`, temperature 1.0, max_tokens 1400 per call, no thinking.
12 probability/expectation trap problems (see problems.json), each with a
famous-wrong-intuition attractor. Both arms see all 12; paired comparison.

- **Arm A (resampling control):** 3 independent samples, each with the SAME
  generic careful-reasoning framing; then 1 reconciliation call shown all three
  attempts, which must explain any disagreement and give a final answer.
- **Arm B (framing diversity):** identical, except the 3 samples each get a
  DIFFERENT structural framing: (F1) enumerate the sample space / direct
  counting; (F2) frequentist thought-experiment, careful about which repetitions
  are observed; (F3) formal random-variables / symbolic manipulation. Framing
  texts length-matched to Arm A's (±20%). Reconciliation call identical to Arm A.

Total: 96 calls (12 x 4 x 2). Budget ≈ $0.60-0.90 metered. Framings are generic
lenses — none encodes any problem's answer (auditable in runner source).

## Measures

1. Final (post-reconciliation) accuracy per arm; paired McNemar counts (b = A
   right/B wrong, c = A wrong/B right).
2. Per-sample accuracy per framing.
3. Per-problem within-arm outcome pattern: unanimous-correct / unanimous-wrong /
   mixed; and same-wrong-answer rate (all 3 samples wrong with the SAME number =
   maximally correlated failure).
4. Error decorrelation: mean pairwise agreement of correctness across the 3
   samples, per arm. The thesis predicts lower agreement (more mixed) in Arm B.
5. Questioning-trigger value: P(final correct | samples disagreed) vs
   P(final correct | unanimous), per arm.

## Null gate (binding)

If Arm B final accuracy <= Arm A final accuracy, the result is reported as
"framing diversity from a single model adds nothing over resampling at equal
compute" — no rescue analyses promoted to headline. Measures 3-5 are reported
descriptively either way. N=12 is an existence probe: exact counts only, no
significance theater; any positive direction is a lead for a bigger N, not a claim.

## Pre-registered predictions (aq-analogy-explorer, before running)

- P1: Arm B - Arm A final accuracy difference will be within ±1 problem (i.e.
  approximately no effect). Confidence ~60%.
- P2: In BOTH arms, among problems where all 3 samples are wrong, the majority
  will share the SAME wrong answer (correlated failure), consistent with attack
  A2 (one model, correlated errors). Confidence ~75%.
- P3: Arm B will show more "mixed" outcome patterns than Arm A (some
  decorrelation from framings), but reconciliation will fail to convert most of
  the extra mixed cases into correct finals. Confidence ~55%.
- P4: Disagreement will predict finals: P(final correct | mixed) sits between
  P(final correct | unanimous-correct)=~1 and P(final correct | unanimous-wrong)=~0
  in both arms — i.e. disagreement is a usable questioning trigger even if
  accuracy doesn't move. Confidence ~70%.

Implication mapping: P1 true + P2 true => internal framing diversity is mostly
cosmetic; genuine diversity must come from OUTSIDE the model (supports the
experience-graph/external-analog direction, once its substrate exists). P1
false in B's favor => cheap harness win; design a bigger-N confirm before
claiming anything.
