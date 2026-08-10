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

The score is:

```text
coverage + 0.50 * connected_fraction + 0.25 * depth_fraction
```

Acceptance also requires at least two matched relations, a connected component of at least two
relations, and at least 50% coverage. This is a small connected-subgraph approximation to
Gentner's systematicity principle, **not a full implementation of SME or higher-order predicate
mapping**. The exact search is factorial and is intentionally bounded to small graphs after the
cheap shortlist.

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

## The honest hard part: representation is given

A human authored and normalized the roles, relation directions, and relation families in this
prototype. That step supplies much of the abstraction. No claim is made that the system can
extract the right graph from a novel problem. An LLM extractor could already have performed
part of the solution, exactly as the Hofstadter objection predicts.

The next evaluation must isolate the increment supplied by retrieval/mapping:

1. Freeze one target graph per problem before any source is retrieved.
2. Give that **identical graph** to random, surface, and structural regimes.
3. Keep source transfer relations hidden until after retrieval so the proposed solution cannot
   select its own source.
4. Give each regime's source to the same downstream planner.
5. Blindly score plan quality and, where safe, target outcomes.
6. Separately score graph extraction against held-out human graphs and run a text-only extractor
   arm with no source library.

If structural retrieval does not beat surface retrieval on those downstream results, the result
is a valid negative. The present receipt only establishes that the matcher can make the intended
cross-domain discrimination when the relational representation is given.

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

## AGE and deployment status

Apache AGE was **not enabled** for this prototype. The in-memory dataclasses make the single
falsifiable discrimination portable and fast. The relation contract maps naturally to AGE
vertices/edges, but moving it there before the discrimination worked would only add substrate,
not evidence. No schema migration, database write, live-loop behavior change, merge, or publish
was performed.
