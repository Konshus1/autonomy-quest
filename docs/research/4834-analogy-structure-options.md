# Task #4834 — Structures for analogy-finding that survive the F2 attack

**Session:** aq-analogy-explorer · **Status:** discussion draft for Kevin · 2026-08-11
**Prior art absorbed:** design doc `close-loop-grounded-evaluator-gate.md` (@3a37aad), BB #2647/#2649/#2650/#2654.

## The constraint everything must satisfy

The refuted matcher's cross-domain discrimination was exact-string family labels
(`structural_analogy.py:297`) — structure smuggled in at graph-authoring time. So the
load-bearing rule for anything new:

> **A relation may only contribute to an analogy match through properties the author
> did not choose as a name.** If a string-label-only retriever reproduces the result,
> the result is void.

Two standing controls, baked into every design below:
- **Label-scramble invariance:** replace every relation/family name with an opaque
  token; retrieval ranking must not change (the refuted matcher fails this instantly).
- **String-ablation reproduction test:** run a labels-only retriever as an arm; if it
  matches the structural retriever's output, the structure did nothing.

And one new trick worth adopting everywhere: a **temporal firewall** — the analog must
have been *recorded before the test problem existed*. Then no one can have authored it
to encode the answer. The system's own experience record gives us this for free;
hand-built source libraries never can.

## Where can relational identity come from, if not names?

This is the actual creative question. Four channels, roughly in increasing order of
label-independence:

**C1. Extensional identity — a relation is what it does in the record.**
In the experience graph, "X was corrected by Y", "X preceded Y", "X cited Y as
evidence", "predicted P, observed Q≠P" are not author-chosen vocabulary; they are
different *record types with different behavior* (direction, cardinality, temporal
order, which principal wrote them). Two episodes share structure when their typed-edge
skeletons align — and the types are load-bearing facts about what happened, not
strings anyone chose for retrieval's benefit.

**C2. Quantitative/behavioral signatures.** Characterize an analog by label-free
dynamics: feedback sign, saturation, conservation, birth–death structure, heavy-tail
vs exponential, one-shot vs repeated sampling. These are computable features of the
relational data (graph motifs, cycle structure, monotonicity of observed series).
The Chao1 case *should* have matched on "repeated sampling with a fat unseen tail",
not on the string `seen_exactly_once`.

**C3. Anti-unification.** Compute the least-general generalization of two relational
episodes over *variables*, scoring by shared-skeleton depth (Gentner's systematicity,
mechanized). Names are variables by construction, so they cannot carry the match.

**C4. Language as the widener, structure as the verifier (Kevin's combination thesis,
inverted from the failed design).** The refuted system used structure (really: strings)
for *retrieval*. Flip it: let the LLM's language understanding do wide, cheap
*generation* of candidate analogs — this is where "the wider you open the analog set"
lives, and language is genuinely good at it — then a structural check does
*discrimination*: is the proposed mapping a consistent relational homomorphism on the
label-free skeleton (C1/C2/C3)? Language proposes, structure disposes. Neither channel
alone passes the gate; the graph earns its keep at the verification step, which is
exactly where the reviewer attacks.

## The refinement: the system's own experience graph as the analog set

Checked against the live DB today: **145,776 goal_events, 772 ralph_learnings (each a
statement + evidence links to decision/task records), 2,556 bb_notes, plus the
governed causal-principle transition log** (append-only, separate-principal
grounding). These are relational records of what actually happened — the closest
thing this system has to "direct experience" rather than linguistic description.

Why this is the strongest version of the thesis:
1. **The graph wasn't authored for analogy.** Its edges (corrected-by, cited-as-
   evidence, predicted-vs-observed, promoted-on-grounding) exist for governance and
   logging. Any analogical signal found in them cannot have been smuggled in at
   authoring time — the F2 attack has no purchase.
2. **Temporal firewall is native.** Episodes predate any test problem.
3. **It unifies with the grounded-evaluator gate.** Same graph, two uses: grounding
   verifies (backward-looking), analogy directs (forward-looking). One substrate,
   verification and generation.

Concrete existence case already visible in the record: the cache-artifact episode
(a cached price accepted as compute cost) and the label-smuggling episode (an
author's label accepted as discovered structure) share the skeleton
*proxy-accepted-in-place-of-target, exposed by an independent re-derivation*. BB #2654
drew that analogy by hand. The question a cheap experiment can answer: can structural
retrieval over the record surface such a sibling for a NEW problem, where a
string-label retriever cannot?

## Honest experiment designs (all < ~$3 unless flagged)

**E1 — Out-of-domain differential (tests Kevin's core claim directly).**
Coding-competent cheap model; N≈12–16 problems, half in-domain (coding/debugging),
half out-of-domain (ecology, queueing, insurance, epidemiology — reuse the frozen
#2650 problem bank where possible, it's already built and paid for). Arms per
problem: (a) baseline; (b) **length-matched** generic "reason about the structure"
control; (c) analogical framing. Blind judging (rubric NOT answer-keyed — F4 fix; a
judge model different from every arm). **The measured quantity is the interaction:
does (c)−(b) exceed in-domain (c)−(b) out-of-domain?** Main effects alone prove
nothing. Null gate: if the interaction CI covers zero, report "no differential
effect" and stop.

**E2 — Experience-graph retrieval vs string retrieval (tests the refinement).**
Take K≈10 held-out episodes with known structural siblings in the record (recorded
earlier — temporal firewall). Query with a paraphrase-hardened description (no shared
vocabulary with the sibling; verified by n-gram overlap check). Arms: C1/C2-style
structural retriever vs string/embedding-of-labels retriever. Success = structural
finds the sibling at rank ≤ r where string does not. This is retrieval-only — no
generation claims — and it is the direct, cheap falsifier of "the graph adds value
beyond language". If string retrieval matches structural, the refinement dies here,
cheaply, before anyone builds anything.

**E3 — Does the retrieved experience *direct* a model? (only if E2 survives).**
Feed E2's retrieved analog (source only — never a hand-written mapping to the
answer) to the cheap model on an out-of-domain problem, vs the length-matched
control. Blind-judged direction/insight. This is where "analogy as bridge to
solution-machinery" gets tested with the analog chosen by the machine, not by us —
closing the "analogy hand-authored to encode the answer" hole that even the honest
#2650 positives had (hand-built three-item source libraries).

**What we never measure:** cost, speed, token counts as outcomes. (F1.)

## Honest tradeoffs to put in front of Kevin

- C1 (extensional identity) is the most defensible but the experience graph's event
  types are few; skeletons may be too coarse to discriminate — E2 will show this as
  a high false-positive rate, which is a legitimate null result, not a design failure.
- C2 (behavioral signatures) is the purest "beyond language" channel but needs
  quantitative traces; much of the record is prose (bb_notes), where extraction
  itself reintroduces a language step. Honest framing: language-in-the-loop is fine
  at *extraction* as long as discrimination is structural — but we must say so.
- C4 is the most likely to actually help a model (language does the wide search) but
  the hardest to attribute: the ablation burden (structure-verifier off vs on) is
  mandatory, or we've proven only that prompting with analogies helps, which is
  known.
- The differential hypothesis (E1) can be true while the graph hypothesis (E2) is
  false, and vice versa. They are separable claims; we should never let one's
  success advertise the other.

## Addendum (Kevin, live): multiple mental models as the driver — adversarial review

Kevin's stated driver: his creativity works by searching for mental models /
structural analogies that fit; multiple models of a domain give more solution
approaches, better behavior prediction, and — via cross-model consistency — a
mechanism for questioning current answers, which LLMs conspicuously lack. He asked
to be adversarially reviewed on this. Review recorded in session dialogue; core
attacks: (A2) framings from one LLM share weights and context → errors correlate →
agreement is not independent evidence (the F2 problem, resurfaced at the model
level); (A4) the LLM deficit may be procedural (no trigger to deploy alternative
framings against its own draft) rather than representational (models absent) —
cheaper hypothesis, test first; (A5) the hard problem is model INDEXING (which
model applies where — scope conditions), which is exactly where the refuted matcher
died.

**E4 — Model-diversity as an error detector (tests the mental-models thesis
directly, ≈$1–2).** Problems with known answers where a cheap model reliably errs.
Arm (a): single-framing self-check ×3 (compute-matched). Arm (b): three genuinely
different structural framings (e.g., stock/flow, equilibrium, adversarial game) +
one reconciliation step that must EXPLAIN any disagreement before answering.
Measures: (1) caught-own-error rate at equal token budget; (2) error correlation
across framings — if pairwise error correlation ≈ 1, multi-framing from one model
is cosmetic and the thesis needs external analog sources (the experience graph) to
supply real diversity. Null gate: (b) ≤ (a) → report "framial diversity from a
single model adds nothing over resampling."

Bridge to the main line: E4's decorrelation measurement is the quantitative test of
whether the analog set must come from OUTSIDE the model (experience graph, C1/C2)
to get genuine diversity — which is Kevin's original intuition about direct
experience, now falsifiable.

## Addendum 2 (Kevin, live): confirmed architecture — independently constructed graph

Kevin confirms the target architecture: the graph is BUILT INDEPENDENTLY of the
query-time LLM — from experience across multiple problem areas, refined by what
happened (support, falsification, scope) — and the LLM's query-time role is to
extract the structure of the CURRENT problem, which is then compared against that
independent graph. Not "ask an LLM for analogies."

What this fixes: analog-set independence (Attack 2's source joint). The graph's
provenance is separate from the model reasoning now, and its relations are refined
by experience the model didn't author at query time — this is what makes it
world-model-like rather than knowledge-base-like.

What it does NOT fix by itself: the EXTRACTION joint. The query-time LLM still
translates the problem into a structural representation, and the comparison needs
a shared type system. #2649 flagged exactly this as the unpriced step: "end-to-end
use requires an extractor emitting the library's exact closed family vocabulary."
If matching operates on names the extractor freely chooses, F2 is rebuilt with
extra steps. Defensible design: a SMALL, CLOSED relation ontology whose types are
enforced by record behavior (precedes / causes / corrects / predicted-vs-observed /
proxy-for / feedback-on ...), extractor constrained to emit into it, matching on
typed topology only; extraction model ≠ graph-authoring model; paraphrase-hardened
inputs; scramble + string-ablation controls standing. Honest residue to state in
any claim: the ontology itself is author-chosen — the defense is that it is small,
fixed, behaviorally enforced, and ablation-tested, not that it is author-free.

Also adopted from Kevin: the graph as CONSTRAINT SURFACE — a graph neighborhood
gives hard, local constraints (these relations exist; those don't; this edge was
falsified) vs the LLM's soft attention over everything. The existing substrate
already carries the refinement machinery (causal_edge: support_count,
falsified_by, scope_conditions, predicted_certainty/surprise; principle
governance: promote/demote). The analogy layer should READ those weights —
an analog whose edges survived falsification outranks raw prose.

Experimental consequence: E2 is the crux experiment and gains two controls:
(1) extraction by a different model than authored the graph entries;
(2) matching code that cannot see surface strings AT ALL (type IDs only) so label
independence holds by construction, and the test burden moves to whether the
extractor types honestly. New E0 (free): a coverage census — mine the existing
record (772 learnings, typed events, principle transitions) for how many distinct
relational skeletons it contains and their domain spread. If cross-domain siblings
barely exist yet, E2 has no fuel and the honest sequencing is: improve logging
richness first, experiment second.

## Addendum 3: source inventory + structural-similarity algorithm options

### Source recovery (Kevin asked)
- **#4834 "Analogies with AI - automated and graphs"**: the ORIGINAL ChatGPT
  conversation survives in `task.details.markdown` (26,725 chars) — recovered and
  committed alongside this doc as `4834-original-chatgpt-conversation.md`. It runs
  from Surfaces & Essences / Hofstadter lineage → relational-not-attribute
  representation → retrieval-vs-mapping split → multi-space embeddings (semantic /
  relational / causal / functional) → discover-the-dimensions epistemology.
  `details` also carries ~35 curated subkeys (kevin_directive_2026_08_06,
  synthesis_2026_08_06, operator_guidance 1–3, refute_llms_cant_jump, …).
- **#4849 "Analogies and Graphs and AI Scientist refining world model"**: the
  SECOND source conversation (VoI explore/exploit governor, AI-scientist frame) in
  its `details.markdown`, plus a consolidated literature list in `description`
  (Gentner SMT, SME, Copycat, relational inductive biases, GNN surveys, and the
  SCAN / SCAR / E-KAR / ANALOGICAL benchmarks — ready-made material for E1/E3).
- **#4863** is NOT an analogy source: it is the completed-task proof-review-UI
  Ralph task (contract + completion packet). Lineage instead: ~45 BB notes
  (#2328→#2655), esp. #2330/#2340 (synthesis+integration map), #2403 (status for
  Kevin), #2438/#2441/#2443 (purpose-drift correction back to source tasks).

### Graph algorithms for finding-by-structural-similarity (the extractor's other half)

Two distinct jobs — keep them separate (the original conversation already said this):
**(R) retrieval** — cheap, indexable signature to shortlist candidates; and
**(M) mapping/verification** — expensive alignment that outputs the actual
correspondences (and is where a bad analogy dies).

| Option | Job | Matches on | Label-free? | Notes at our scale |
|---|---|---|---|---|
| WL refinement / WL kernel (graph2vec, GIN) | R | iterated neighborhood structure over type IDs | by construction if seeded with closed type IDs or uniform labels | THE workhorse; color histograms → cosine/minhash index |
| Graphlet / orbit signatures (GDV) | R | pure local topology (3–5-node motifs) | fully | strongest label-independence; may be too coarse on small sparse episode graphs |
| Anonymous walk embeddings | R | walk shape only | fully | label-free by construction |
| Spectral (Laplacian / heat-kernel sigs) | R | global shape | fully | coarse; size-sensitive |
| struc2vec / GraphWave / RolX | R (node-level) | structural ROLES across graphs | yes | "find ELEMENTS by structural similarity" literally — role match without shared vocabulary |
| Subgraph isomorphism (VF2/VF3; Cypher patterns) | M | exact typed topology | types only | AGE/Cypher pattern match IS this; the refuted matcher was a string-typed degenerate case |
| Maximum common subgraph (McSplit) | M | largest shared skeleton | types only | the MCS *is* the analogy core, Gentner-style; NP-hard but episode graphs are tiny |
| Graph edit distance (bipartite approx) | M | edit cost | tunable | cheminformatics workhorse |
| SME (Structure-Mapping Engine) | M | relational alignment w/ systematicity | predicate types only | purpose-built for analogy; our closed ontology = its predicate vocabulary |
| Gromov-Wasserstein OT | M (or R via sketches) | metric structure of each graph, NO shared vocabulary needed | fully | the purest "structure independent of names" comparison; POT library |
| IsoRank / REGAL / FINAL | M | global alignment | REGAL has label-free mode | network-alignment lineage |

**Recommended shape:** two-stage R→M. Retrieval: WL-over-closed-type-IDs
histograms (primary) + graphlet vector (pure-topology control arm). Mapping: typed
MCS or SME-style alignment, with Gromov-Wasserstein as the label-free referee arm.
Scale note: hundreds-to-thousands of episodes, each graph tens of nodes — NP-hard
alignment is FINE here; we need controls and honesty, not scalability.
F2-resistance: graphlets/anonymous-walks/GW are label-independent by construction;
WL/MCS/SME inherit exactly the closed-ontology honesty burden named in Addendum 2
(scramble-invariance holds by construction if matching sees only type IDs; the
attack surface is the extractor's typing, tested by cross-model extraction).
