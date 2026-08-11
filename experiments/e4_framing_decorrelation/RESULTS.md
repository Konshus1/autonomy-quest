# E4 results — multi-framing decorrelation (task #4834)

Run 2026-08-11, claude-haiku-4-5, temperature 1.0, 96 calls, **$0.52 metered**.
Design + predictions frozen at fe124b5 BEFORE the run. Raw transcripts:
`raw_results.json`. Scoring: exact-numeric, judge-free.

## Headline (pre-registered measures)

| | Arm A (resample ×3) | Arm B (3 framings) |
|---|---|---|
| Final accuracy (post-reconciliation) | 10/12 | 11/12 |
| Sample accuracy | 31/36 | 31/36 |
| Outcome patterns | 9 unan-right, 3 mixed | 8 unan-right, 4 mixed |
| Pairwise correctness agreement | 0.83 | 0.78 |
| Final correct given samples unanimous | 8/8 | 5/5 |
| Final correct given samples disagreed | 2/4 | 6/7 |
| McNemar | A-right/B-wrong 1 | A-wrong/B-right 2 |

**Null gate: not triggered (B > A), but per pre-registration a +1/12 difference
is inside the "≈ no effect" band and is a lead, not a claim.**

## Prediction scoring (honest)

- **P1 (no meaningful accuracy difference, ±1): CONFIRMED.** 10 vs 11.
- **P2 (all-wrong problems share the same wrong number): NOT EVALUABLE.**
  Zero problems had all 3 samples wrong in either arm — Haiku 4.5 is stronger
  on these classic traps than the design assumed; 8-9 of 12 problems were at
  ceiling. The discriminating subset was only 4-5 problems. (Correlated-error
  evidence still appeared at sample level: the classic attractors recur —
  7.5 for bus-wait in both arms, 0.25 twice in Arm A's monty-fall.)
- **P3 (B more mixed patterns; reconciliation fails to exploit): HALF WRONG.**
  B did show more mixed patterns (4 vs 3) and lower agreement (0.78 vs 0.83) —
  mild decorrelation is real. But reconciliation exploited it far better than
  predicted: 6/7 disagreed cases resolved correctly in B vs 2/4 in A.
- **P4 (disagreement is a usable questioning trigger): CONFIRMED, strongly.**
  Across both arms, unanimity → final correct 13/13; disagreement → 8/11.
  In this run unanimity was a *perfect* predictor of correctness.

## Two findings not in the predictions

1. **The framings have genuinely different error profiles — which is both the
   mechanism and a confound.** Per-framing sample accuracy: enumerate 8/12,
   frequentist 11/12, algebraic 12/12. Diversity across framings is real, but
   Arm B's edge is confounded with *framing quality* (algebraic simply beats
   enumerate on probability traps, where naive enumeration of conditional /
   unbounded processes goes wrong). A clean follow-up needs an F3×3 arm: if
   "best framing, resampled" matches Arm B, the win is framing selection, not
   diversity per se.
2. **Reconciliation is not majority voting — it re-reasons, for better and
   worse.** It rescued a minority-correct answer (die-6-all-even: two 3s and
   one 1.5 → correctly picked 1.5) and was also seduced by a well-argued
   minority-wrong answer against a correct majority (bus-wait B: two 15s, one
   7.5 → picked 7.5). Simple majority vote would have scored 10/12 in both
   arms. The reconciler adds variance, not guaranteed lift.

## Honest interpretation for the mental-models thesis

- The **accuracy** claim (framing diversity beats resampling at equal compute)
  is NOT supported at this N: +1/12, inside the pre-registered noise band.
- The **questioning-trigger** claim has real support: cross-framing
  disagreement was a perfect risk flag in this run (no false-confidence case —
  no unanimous-wrong — occurred, though the design cannot rule them out; a
  weaker model or harder tail would produce them, and that is exactly where
  the trigger would matter or break).
- The **decorrelation** number (0.83 → 0.78) says internal framing diversity
  buys only mild independence — directionally consistent with attack A2
  (one model's framings share errors) but not the near-1 correlation the
  strong version of A2 predicted. Partly unmeasurable here due to ceiling.

## Limits

N=12, one model, one run, ceiling effects on ~8 problems, framing-quality
confound (finding 1), no unanimous-wrong events to test the trigger's failure
mode. Nothing here generalizes beyond "existence probe on classic probability
traps with Haiku 4.5."

## What would make this worth a bigger run

Harder problem tail (target 30-60% baseline accuracy), an F3x3 arm to
deconfound quality vs diversity, and a second model family to test whether
cross-MODEL disagreement is a stronger trigger than cross-framing — the
cheapest available proxy for "diversity must come from outside the model."
