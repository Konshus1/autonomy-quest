# M1 inclusion/exclusion audit trail (v2)

Rule: **The target prompt states requirements and observable constraints, never the organising mechanism; admitted tasks must leave multiple materially different correct organizations.**

> Correction: v1 passed a keyword audit but was retracted after semantic review found behavioral aliases of mechanisms. V2 admits only independently reviewed candidates. BB #2516 records the retraction.

## Included after independent semantic review

| id | domain | why the mechanism remains withheld | hidden structure (never sent to DIRECT) |
|---|---|---|---|
| B01 | publishing and field operations | Sections and formats are domain facts; tree/passes are not forced, with direct rendering, token streams, per-format models, and normalized documents viable. | A semantic report tree, followed by audience-pruning, reference-resolution, then format-specific emitters. |
| B03 | physical security and organizational policy | Canonical procedural checks, tables, predicates, rule objects, or truth tables can satisfy behavior without the hidden policy algebra. | Small composable decision values (`applicable`, verdict, reasons, dependencies) combined by a policy algebra, separate from request data. |
| B05 | configuration and developer tooling | Provenance may be recomputed, logged in parallel, represented by field objects, or derived during parsing; annotated candidates are not forced. | A tree of annotated candidate values (value + origin + status), reduced only after overlay and validation. |
| B07 | customer communications and document production | Independent templates, annotated fragments, emitters, staged layout, and channel-specific models remain materially different. | Channel-independent content nodes with capabilities/constraints, then channel layout passes. |
| B08 | oral-history archives and collaborative editing | Snapshots with repair tables, alias maps, offset anchors, edit scripts, and immutable lineage are test-equivalent alternatives. | Persistent segment lineage (stable logical anchors plus revisioned descendants), with edit operations and exports as projections. |
| B11 | museum localization and accessibility | Locale/medium cross-product allows templates, field formatters, branching serializers, policy objects, or prelocalized models. | Semantic label fields transformed through independent locale and medium policies before serialization. |
| B12 | ecological data reconciliation | Audit copies, relational tables, representative records, pair flags, and assertion overlays are materially different. | Raw immutable observations, canonical identities, and explicit reconciliation assertions, with summaries projected from resolved identities. |
| B13 | culinary publishing and adaptation | Deep-copy mutation, constraint search, substitution functions, ingredient objects, and template regeneration remain viable. | A semantic recipe with linked ingredient references, adapted by ordered whole-recipe rewrite passes rather than string substitution. |
| B14 | civic administration and meeting records | Annotated copied agendas, parallel planned/actual structures, commands, snapshots, or replay remain viable. | Stable agenda entities plus a meeting event log, producing document-specific projections. |
| B15 | packaging production and approval governance | Version counters, dirty dependency sets, copied candidates, immutable values, or scoped review records remain viable. | Content-addressed region versions, candidate manifests, and scoped review attestations rather than approvals stored on mutable candidates. |
| B16 | community agriculture and seasonal planning | Calendar matrices, rich objects, procedural validators, constraint engines, and copied next-season drafts remain viable. | One declarative season model plus facts/constraints and several projections; next season is an overlay carrying choice provenance. |
| C03 | Collaborative writing | Nested content permits typed recursion, flat parent-index tables, nested dictionaries, visitors, or Composite objects. | Composite: individual content items and nested groups share operations used for traversal and aggregation. |
| C05 | Customer communications | Orderable capabilities permit wrappers, configured renderer, explicit pipeline, higher-order functions, or one-pass rewriting. | Decorator: behavior is added through composable wrappers around a renderer. |
| C06 | Museum operations | History-dependent validity permits transition table, procedural guards, rule objects, event reduction, or polymorphic states. | State: lifecycle-specific objects determine which proposals are valid and what follows. |
| C20 | Clinic administration | Session facts/actions/screens permit reducer plus formatter, one stateful object, transition table, command handlers, or MVC. | Model-View-Controller: session data, screen rendering, and action interpretation occupy separate collaborating roles. |

## Excluded candidates

| id | title | reason |
|---|---|---|
| B02 | Museum object identity notebook | Historical pair evidence plus transitive identity, filters, withdrawals, and permutation invariance substantially force evidence-relation/component semantics. |
| B04 | Theatre cue-book revision tool | Relative cue chains/cycle errors and preserved references force dependency and durable identity; only revision overlays were withheld. |
| B06 | Studio move planner | Shared previewable actions executed on interchangeable backends behaviorally state a backend-neutral plan/interpreter. |
| B09 | Board-game rules edition workbench | Replace/insert/disable/qualify operations, origin reporting, and order independence behaviorally alias declarative patches. |
| B10 | Scientific sample chain-of-custody | Subdivision/recombination ancestry and preserved disputes inherently define historical provenance graph semantics. |
| B17 | Manuscript cross-reference editor | Stable IDs, move-surviving references, derived numbering, aliases, placeholders, and identity diff nearly restate hidden organization. |
| B18 | Emergency shelter handover board | Effective time plus learned time with both query axes is the behavioral definition of bitemporal data. |
| C01 | Household Scene Coordinator | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C02 | Publication Production Kits | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C04 | Submission Ceremony | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C07 | Field Station Updates | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C08 | Trip Guidance Policies | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C09 | Instrument Reading Gateway | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C10 | Community Venue Desk | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C11 | Workshop Setup Copies | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C12 | Festival Program Assembly | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C13 | Community Tool Requests | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C14 | Grant Decision Vocabulary | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C15 | Archive Entry Access | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C16 | Research Evidence Board | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C17 | Oral History Preparation | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C18 | Field Journal Boundary | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| C19 | Volunteer Roster Collection | Strict reviewer found the public contract behaviorally encodes the named pattern or narrows the design to that role split. |
| AE01 | Shared worker permits | Clocked request/renew/release behavior strongly cues expiring leases; fencing is not observably required. |
| AE02 | Resumable multi-step migration | Effect-after-crash semantics are impossible without an effect receipt/idempotency cooperation contract. |
| AE03 | Explainable configuration choice | Superseded by broader B05; retained as the one comparatively clean v1 item rather than duplicated. |
| AE04 | Duplicate-safe command handling | Repeated command identity and recorded result strongly cue an idempotency journal; external at-most-once is underspecified. |
| AE05 | Stable rollout assignment | Close-to-percentage and minimally-disturbed requirements lack executable numeric tolerances. |
| AE06 | Selective rebuild planner | Affected-only rebuild, cycle rejection, and valid order behaviorally specify reverse reachability plus topological ordering. |
| AE07 | Burst-tolerant admission | Average rate, burst bound, and exact wait time are the textbook behavioral definition of token bucket. |
| AE08 | Fresh-enough reads during refresh | Named usable/refresh-needed/unusable states and two deadlines strongly disclose the intended state machine. |
| AE09 | Fair retry scheduling | Delay, fairness, and inspect behavior are underspecified without an executable scheduling policy. |
| AE10 | Capability-compatible plugin selection | Compatibility filter plus deterministic priority/name rank is too narrow to discriminate architecture. |
| AE11 | Desired-state reconciliation | Desired/observed CRUD convergence behaviorally aliases a state diff/reconciler and has a narrow solution space. |
| AE12 | Historical account reconstruction | Past-sequence answers and never-rewritten history effectively state append-only event reduction. |
| AE13 | Bounded batch execution report | Cancellation source and concurrency result contract are underspecified. |
| AE14 | Chunk-independent record decoder | Length-prefix/chunk behavior forces a narrow incremental parser bookkeeping exercise. |
| AE15 | Nested reversible edits | Nested transaction semantics plus no-copy constraint largely force overlays or undo journals. |
| AE16 | Stable continuation through changing data | Stable continuation under front inserts and compound tie fields behaviorally define keyset pagination. |
| AE17 | Order-preserving update compaction | Minimal final updates in last-finalization order dictate retaining each last occurrence; narrow puzzle. |
| AE18 | Repeated alert suppression | Suppression window cues timestamp map and bounded-memory requirement is not executable. |
| AX01 | Leaky rate limiter prompt | Names the organizing mechanism verbatim. This violates the requirement-only selection rule. |
| AX02 | Leaky rebuild prompt | Supplies both structural stages rather than only behavior. |
| AX03 | Leaky nested edit prompt | States the representation the analogy is meant to contribute. |
| AX04 | Leaky paginator prompt | Names the algorithm and comparison key. This violates the requirement-only selection rule. |
| AX05 | Leaky migration prompt | Prescribes the intended durability protocol. This violates the requirement-only selection rule. |

## Honest boundary

- The independent semantic audit checks that several materially different organizations remain possible; it is stronger than keyword absence but still human/model judgment.
- Four admitted `C*` items intentionally carry `model_prior_risk: high` because their hidden structures are classical named patterns. This risk is frozen and must be reported separately rather than hidden.
- Absence from a model’s pretraining cannot be observed. This corpus operationalizes **withheld from the prompt**, not proven absent from model priors.
- M1 does not establish executable tests, retrieval, mappings, generations, correctness, or an elegance effect.
