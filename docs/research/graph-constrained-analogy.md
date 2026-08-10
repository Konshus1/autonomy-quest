# Graph-constrained structural analogy prototype (#4834)

Status: **research prototype, advisory only, not wired to the live AQ loop**  
Branch: `task/4834-graph-constrained-analogy`  
Cost: **$0**. The prototype and tests make no network or model calls.

## Source contract

This build follows the task #4849 synthesis (`analogies_worldmodel_synthesis.md`), BB #2330,
and BB #2430. The important correction from #2430 is that a frame is a **situation with roles
and relations**, not a noun or attribute bag. The seven-layer synthesis says retrieval must be
cheap before mapping becomes expensive, analogy may only propose, a causal edge holds the
proposal as a hypothesis, and later surprise grades it.

The legacy `ralph_portable/frame_expansion.py` attribute-to-dimension similarity path does not
implement that contract. It is already labelled as a retired/known-artifact mechanism in the
live sync path. This prototype does not add another embedding or token heuristic to it;
`ralph_portable/structural_analogy.py` is its proposed relational replacement. Compatibility
code remains untouched because this branch is a research proposal, not a live-loop cutover.

## What was built

### Stage 1: cheap retrieval signature

`RetrievalSignature` contains exactly:

```text
{goal, constraints, failed_predictions, domain}
```

`cheap_shortlist` reads only those fields. Goal, constraint, and failed-prediction overlap get
92% of the score. Domain gets 8% as a cost hint. A cross-domain reserve prevents domain from
becoming a relevance gate. A control object whose graph and surface properties raise on access
proves Stage 1 does not inspect them.

### Stage 2: structural mapping

`structural_match` enumerates one-to-one role maps over the small shortlist, then aligns directed
relations when:

1. their normalized relation family agrees; and
2. their subject/object endpoints are preserved by the role map.

Predicate wording may differ. Surface labels, summaries, domains, and attribute/property rows
contribute zero. For a fixed map, relation correspondences are one-to-one.

The hardened score is:

```text
0.35 * source_coverage + 0.65 * target_coverage
+ 0.50 * connected_fraction + 0.25 * depth_fraction
```

Acceptance also requires at least two matched relations, a connected component of at least two
relations, and at least 40% coverage in each direction. The asymmetric weighting tolerates a rich
source or partial extracted target while still penalizing tiny cherry-picked coincidences. The
role search maps the smaller role set into subsets of the larger graph, so irrelevant source roles
no longer force rejection. This is a small connected-common-subgraph approximation to Gentner's
systematicity principle, **not a full implementation of SME or higher-order predicate mapping**.
The exact search is factorial and is intentionally bounded to small graphs after the cheap
shortlist.

### Stage 3: plan projection

Only after selection does `project_plan` read the source's transferable relations. It substitutes
mapped target roles and creates explicit `needs_target_instantiation` roles for source roles that
did not exist in the observed target. The result is an advisory plan skeleton:

```text
introduce proposed_barrier
require interruption(proposed_barrier, target.network)
require protection(proposed_barrier, target.systems)
```

This is not an executable action and is not authority. A later target planner must ground
`proposed_barrier`, write a pre-action `planning_prediction`, and, if used, submit a low-formality
`causal_edge` proposal with scope and a falsifier. This branch deliberately does not write either
table.

## Load-bearing discrimination

The target is lateral malware propagation. Stage 1 is under real pressure: five source cases,
shortlist size two. It ranks the same-domain malware scanner first and the cross-domain wildfire
case second. A plain surface baseline also selects the scanner.

Stage 2 rejects the scanner (zero relation correspondences) and selects the wildfire case:

```text
fire      -> process
fuel      -> network
community -> systems

propagation(fire, fuel)       -> propagation(process, network)
exposure(fuel, community)     -> exposure(network, systems)
```

The role IDs are deliberately non-identical. Stage 3 projects the firebreak's interruption and
protection relations into the malware target. The frozen receipt is
`experiments/graph_analogy/receipt.json`.

Measured source choices on the same target:

| regime | selected source |
|---|---|
| random (seed 4834) | `double_entry` |
| surface | `cybersecurity_malware_scanner` |
| structural | `wildfire_firebreak` |

This proves one discrimination over a curated graph. It does **not** show downstream plan
usefulness or mission improvement.

## Controls that can come back negative

The tests include:

- `k < N` shortlist pressure and same-domain preference at Stage 1;
- explicit surface retrieval selecting the distractor;
- opaque/non-identity role mapping;
- equal-count connected relations beating independent relation matches;
- identical text/signature/family multiset losing when shared-role topology is broken;
- surface/domain/role renaming preserving the structural verdict;
- twenty attribute matches adding exactly zero structural score;
- abstention when no accepted structure exists;
- projected relations resolving only to mapped or explicitly proposed target roles.

Run:

```bash
uv run pytest tests/test_structural_analogy_discrimination.py tests/test_structural_analogy.py -q
uv run python experiments/graph_analogy/run_demo.py \
  --output experiments/graph_analogy/receipt.json
```

## Extraction follow-on and downstream usefulness verdict

The follow-on under [`experiments/graph_analogy_extracted/`](../../experiments/graph_analogy_extracted/)
ran the missing extraction half on one novel plain-text ransomware situation. A subscription model
was blinded from source cases, transfer relations, and winner identity. It could extract only
text-grounded observations and had to reject inferred interventions. The emitted graph retained
two verbatim-evidenced relations and no action:

```text
infected_laptop --propagation--> peer_machines --exposure--> payroll_computers
```

A held-out human reference used `encryption_process -> peer_connections -> payroll_computers`, so
the extraction was imperfect at role boundaries. The family topology nevertheless survived. The
deterministic matcher rejected the same-domain scanner, selected the cross-domain firebreak, rejected
the same-family rewired control, accepted an opaque-label control, and abstained when the one accepted
structure was removed.

Under Kevin's sharpened baseline-absence criterion, the downstream result is **neither**. Identical-
instruction baseline, surface, and structural planner arms all proposed restricting peer paths, so
analogy did not supply an element the no-analogue planner lacked. There is a secondary retrieval
finding: on frozen source-backed candidates, structure ranked dependency-aware peer-path containment
above the alert-dashboard / continued host-by-host candidate, surface similarity ranked them in the
opposite order, and a blinded judge preferred the structural candidate. That is evidence beyond
surface similarity but not the requested planning-usefulness existence proof because the baseline
already found the useful direction.

Five further prospective candidates were rejected at the baseline gate before source/extraction arms:
sacrificial witnesses, recursive lease surrender, capture-recapture, physical trust-epoch rotation,
and Chao1 unseen-class estimation. The strong no-analogue planner produced every intended element.
Their complete prompts and outputs are frozen in `strict_existence_probes.json`.

This one-case result does not establish general extraction reliability, live action success, or
mission improvement. The still-open evaluation is a multi-problem, independently labeled benchmark
and safe target outcomes. An LLM extraction still supplies part of the abstraction, so the claim is
only the incremental retrieval/ranking result after the graph was frozen.

## Reconciliation with the seven layers

| synthesis layer | this branch |
|---|---|
| L0 concept dimensions | not used for relevance; attribute rows are excluded |
| L1 episodes | represented as `Situation` frames |
| L2 analogy | implemented: signature retrieval, mapping, plan projection |
| L3 schema compression | not implemented |
| L4 causal edges | projection is shaped as an advisory precursor; no writes |
| L5 action/directness | remains `needs_target_instantiation`, not executable |
| L6 surprise | follow-up must compare pre-action direction to outcome |
| L7 VoI governor | bounded shortlist is the cost control; no live governor wiring |

## Native file-backed path and deployment status

Apache AGE is **not required**. `load_situation_file` and `load_situation_library_file` load the
portable JSON contract directly into the pure-Python matcher. Extractor audit fields such as evidence
spans remain in the file but are ignored by matching. The extracted-graph receipt and tests execute
this path with no AGE, database, network, NetworkX, or optional dependency.

The relation contract could still map to AGE vertices/edges where AGE is available, but AGE supplies
storage rather than evidence. No schema migration, database write, live-loop behavior change, merge,
or publish was performed.

## Cost-lever continuation: one existence proof

The cheap-model follow-on under
[`experiments/graph_analogy_cost_lever/`](../../experiments/graph_analogy_cost_lever/)
found one frozen baseline-fails + surface-fails + structure-succeeds triple. On unseen failure-class
estimation, Haiku 4.5 alone and with a lexical protocol-coverage source omitted the Chao1
calculation. The structurally retrieved ecological rare-species case caused Haiku to compute 80
unseen and about 180 total classes; Fable 5 computed the same core element without an analogue.
A retrieval-blind judge scored frontier 5/5, structural Haiku 4/5, baseline 2/5, and surface 1/5.
Structural Haiku did not explicitly make the final no-release recommendation, so the claim is core
quantitative-plan parity, not complete answer parity.

The structural source beat a plausible two-of-three-relation near miss (`1.52666667` versus
`1.41666667`) despite two source-noise relations. Scrambled topology was rejected, opaque renaming
preserved the exact score, a partial target remained accepted, and an extra target-noise relation
preserved the ordering. The frozen boundary is informative: when extraction omits the third
relation that distinguishes the true source from the near miss, topology alone cannot distinguish
them; three core matches diluted among eight source edges also fall below the 40% guard.

All final calls used verified Claude Max subscription auth. CLI price-equivalent envelopes were
$0.02432 for structural Haiku and $1.00386 for frontier Fable, a realized 41.28x ratio. This is a
cost-lever existence result but does **not** establish a 100x realized saving, a benchmark success
rate, or downstream outcome improvement.

