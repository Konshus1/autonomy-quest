# M1 inclusion/exclusion audit trail

Rule: **The target prompt states requirements and observable constraints, never the organising mechanism.**

## Included

| id | domain | decision rationale | hidden structure (withheld from DIRECT) |
|---|---|---|---|
| E01 | concurrency | Specifies safety, recovery, API, and determinism but not how abandoned capacity is detected or stale holders are excluded. | expiring leases with fencing generations |
| E02 | data_migration | States interruption and observability requirements without prescribing how intent and completion are represented. | durable intent/effect checkpoint protocol |
| E03 | configuration | Names sources and desired explanation but withholds the compositional representation and resolution mechanism. | ordered provenance-bearing candidates folded by precedence |
| E04 | messaging | Defines identity and replay semantics but does not tell the implementation what durable fact to store. | idempotency-key result journal |
| E05 | feature_delivery | States stability and minimal movement but withholds the coordinate system used to achieve them. | stable digest mapped onto a fixed numeric ring |
| E06 | build_systems | Defines observable affectedness, ordering, cycle behavior, and determinism without naming graph algorithms. | reverse dependency closure followed by stable topological order |
| E07 | traffic_control | States rate and burst invariants but not the state representation or update equation. | continuously refilled bounded credit reservoir |
| E08 | caching | Defines states behaviorally and asks for election but does not reveal transition representation or coordination structure. | entry state machine with soft and hard deadlines plus single refresh claim |
| E09 | scheduling | Specifies delay, fairness, determinism, and explanations without naming the scheduling data structure. | per-job next-ready timestamps in a stable priority queue |
| E10 | plugins | States compatibility and stability requirements without prescribing representation or selection pipeline. | set containment filter then stable lexicographic ranking |
| E11 | resource_management | Defines convergence and audit behavior but does not name the pure diff organization. | pure canonical state diff with convergent operation ordering |
| E12 | audit_history | The no-rewrite requirement is a constraint, while the event/reduction organization and snapshot role remain withheld. | append-only events reduced into state with verifiable snapshots |
| E13 | parallel_execution | States output algebra and bound without prescribing execution or result representation. | indexed result envelopes with bounded worker pool |
| E14 | stream_processing | Specifies wire format and chunk invariance but withholds the phase organization. | incremental parser with explicit header/body phases |
| E15 | transactions | Defines nested semantics and rules out naive full copies but leaves the representation and merge behavior unstated. | stack of overlay journals with tombstones |
| E16 | pagination | States continuation invariants without identifying the comparison key or encoding strategy. | keyset continuation over a frozen compound boundary |
| E17 | storage | Defines semantic equivalence, minimality, and order but withholds the one-pass organization. | reverse scan selecting final occurrence then stable reversal |
| E18 | monitoring | Specifies temporal behavior and boundedness without naming the stored record or cleanup strategy. | per-identity expiring aggregation records with lazy eviction |

## Excluded

| id | quoted leak | reason |
|---|---|---|
| X01 | `token-bucket rate limiter` | Names the organizing mechanism verbatim. |
| X02 | `reverse dependency graph and topological sort` | Supplies both structural stages rather than only behavior. |
| X03 | `stack of overlay maps with tombstones` | States the representation the analogy is meant to contribute. |
| X04 | `keyset pagination using a compound created_at/id cursor` | Names the algorithm and comparison key. |
| X05 | `write-ahead intent log and completed-step checkpoints` | Prescribes the intended durability protocol. |

## Scope of this audit

- It establishes that 18 requirement-only prompts survive the declared cue audit and have human-readable rationales.
- It does **not** establish that no paraphrase could cue a model; that remains a semantic limitation and must be considered in interpretation.
- It does **not** establish that the later tests, analogies, retrieval, or scoring harness work. Those are later milestones.
