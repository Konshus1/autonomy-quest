# Elegance Under Withheld Structure

This directory implements the successor experiment specified by BB decision #873,
Amendment 2.  M1 freezes candidate selection **before** any generation harness is
built.

## M1 admission rule

A target prompt states the requirement and observable constraints, never the
organising mechanism. `corpus_candidates.json` preserves both admitted and rejected
candidates.  For admitted candidates it records the hidden structure, terms that
would leak it, a human admission rationale, and a hand-built cross-domain analogy.
Only `requirement` may later be sent to the DIRECT arm.

The audit is intentionally discriminating:

```bash
python3 experiments/elegance_withheld_structure/verify_corpus.py
```

It requires at least 15 admitted tasks across at least eight domains and rejects an
included prompt containing any of its predeclared mechanism cues.  Rejected examples
must contain a verbatim, machine-detectable leak.  Semantic judgment cannot be fully
automated; the per-item rationales make that boundary inspectable instead of claiming
a keyword scan proves absence of every synonym.

No model outputs, scorer, or experiment harness belongs to M1.  Later milestones may
consume this frozen corpus but must not silently rewrite it after seeing outcomes.
