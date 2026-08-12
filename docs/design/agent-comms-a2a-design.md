# Agent communications and A2A design

**Status:** design proposal for operator decision

**Date:** 2026-08-12

**Scope:** inter-instance and intra-workflow communications, operator UI, and an A2A boundary

**Code changes:** none; this document is the only artifact

## Executive decision

Adopt the operator's two-layer model, with one important refinement:

1. **Intra-instance:** use a durable, instance-local mailbox as the bus. A tmux server inside the AQ app container may orchestrate and wake multiple agents that share that container, but tmux is a delivery adapter, not the message store or protocol.
2. **Inter-instance:** put a network A2A adapter at every instance boundary and route through a host-authoritative relay. Start with a small AQ HTTP/mailbox contract behind the existing management API. Preserve an adapter seam for official Agent2Agent (A2A) 1.0, then add a conforming A2A façade only when AQ has real delegated, long-running agent tasks or needs third-party interoperability.

The first production slice should be one-way and observational: the host polls each replica's loopback-published management `/health`, records host-observed health/status in the parent's durable comms store, and shows it in the existing React UI. It should not accept commands. The next slice may pull a replica-authored outbox containing status and experiment-result messages. Parent-to-replica work requests and multi-agent workflow dispatch come later, behind typed queues and the existing authority gates.

## 0. Load-bearing transport constraint

**Tmux cannot be the inter-instance transport.** Ralph's workers are host-level macOS processes sharing one host tmux server. AQ agents run in separate Linux Docker containers. A container's tmux server, socket namespace, process namespace, and terminal panes are not the Mac host's, and two isolated Compose projects do not share a tmux server. No design in this document uses tmux across a container/host or container/container boundary.

The boundary transport must be HTTP and/or a durable mailbox polled over HTTP. It must cover:

| Direction | Reachable route |
| --- | --- |
| Linux container -> macOS host | `http://host.docker.internal:<bus-port>` on Docker Desktop, with a host listener deliberately made reachable on that path and authenticated per instance |
| macOS host -> Linux container | `http://127.0.0.1:<replica-app-mgmt-port>` using the unique port selected and recorded by the host |
| container -> container | Prefer A -> host relay -> B, preserving isolated Compose networks; an explicit shared external Docker network is an optional later topology |
| macOS host agent -> Linux container agent | Host relay or host client -> replica's `127.0.0.1:<app_mgmt_port>` -> instance-local mailbox/dispatcher |

Docker documents that `host.docker.internal` resolves to the host's internal address on Docker Desktop, and that host access to a container requires a published port ([Docker Desktop networking](https://docs.docker.com/desktop/features/networking/networking-how-tos/)). A host service bound only to `127.0.0.1` must **not** be assumed reachable through `host.docker.internal`; the exact listener binding must be tested on the target Docker Desktop/macOS version. Do not solve that by casually binding an unauthenticated bus to `0.0.0.0`. Either bind to an appropriately firewalled host interface or let the host poll replica outboxes through their already published loopback ports.

The current Compose file declares no shared external network. Compose therefore creates a project-scoped `<project>_default` network; distinct `COMPOSE_PROJECT_NAME` values produce distinct default networks ([Docker Compose networking](https://docs.docker.com/compose/how-tos/networking/)). That isolation should remain the default.

## 1. Research snapshot and source qualification

The checked-out branch `task/jump-mechanism-live-wiring` at `e91d777` is divergent from the local `origin/main` at `22b3208`. Two user-named files are not present in the checked-out tree but are present in `origin/main`:

- `ralph_portable/host_replica_stack.py`
- `scripts/host_replication_daemon.py` (the actual path; it is not under `ralph_portable/`)

`origin/main` contains both the replication work and the interpreted-workflow work, so this document uses `origin/main@22b3208` as the integrated AQ snapshot for those features. Unqualified AQ paths below refer to that snapshot. The existing comms shell is also present in the checked-out branch and has the same relevant contract.

The Ralph reference was inspected in the adjacent local `talkingback-fastapi` repository at `c919e447`. A2A statements were checked against the official 1.0.0 specification on 2026-08-12.

## 2. Current state, verified from code

### 2.1 Inter-instance: replicas are isolated islands

The premise is confirmed.

- `ralph_portable/host_replica_stack.py:55-68` assigns each replica a distinct `aq-replica-<shortid>` Compose project. Its module contract explicitly describes an isolated stack (`:12-17`).
- `allocate_replica_identity()` generates a host-random project name and a fresh `urn:uuid:` instance ID (`:263-286`). `render_secret_file()` puts that identity in `AQ_INSTANCE_ID` and generates distinct credentials (`:357-380`).
- Four unique host ports are probed (`:289-315`, `:510-513`), then the overlay publishes Postgres, governance, UI, and app-management ports on `127.0.0.1` (`:318-354`). The returned execution record includes the port map and health URL (`:530-545`).
- The host sets a distinct `COMPOSE_PROJECT_NAME` and secret/state files before Compose stand-up (`:558-572`). The base `docker-compose.yml:155-158` has project-namespaced volumes and no external network. Docker Compose's implicit default network is therefore project-scoped.
- The host polls the new replica's loopback-published `/health` (`host_replica_stack.py:408-448`, `:590-605`). That proves host-to-replica reachability only. No parent URL, comms-bus URL, parent credential, shared network, callback, or relay is injected into the replica.
- `scripts/host_replication_daemon.py:219-250` calls stand-up and records an outcome in a host ledger. The ledger deliberately does not mutate the guest-writable proposal store (`:136-166`).

The result is its own database volume, workspace volume, auth volume, default network, credentials, management API, and identity. It has no message path back to its parent or siblings.

One requested premise needs narrowing: the full stand-up record and `replica.json` contain the four-port map, but the daemon ledger retains only `project`, `instance_id`, and `health_url` (`scripts/host_replication_daemon.py:233-242`), while its summary drops the port map (`:228-230`). The original management `ralph_replication` proposal row is not updated. The host therefore has enough information to reach the replica, but there is not yet one authoritative parent topology record. Creating that host-owned registry is prerequisite work.

The replication trust boundary is strong and directly relevant to comms:

- Guests propose; the host executes (`ralph_portable/replication_request.py:1-8`, `:63-70`).
- Only approved/auto-approved status may enter host execution (`:81-87`).
- The host enforces an across-generations cap and a fail-closed memory gate under a host flock (`host_replica_stack.py:19-28`, `:492-508`).
- Guest modifications cannot set `AQ_REPLICATION_*`, credentials, ports, `AQ_INSTANCE_ID`, governance URLs, or other host-security keys (`ralph_portable/replication_modifications.py:53-78`).

Comms must preserve, not route around, these controls.

### 2.2 Intra-instance: a durable demo log and UI shell, not a bus

AQ does have agent-comms scaffolding, and it is inert with respect to the loop.

What is real today:

- `management/api/app.py:194-216` implements `GET /api/agent-comms` and `POST /api/agent-comms`. `CommIn` accepts `from_handle`, optional `to_handle`, non-empty `text`, and `kind` defaulting to `message`.
- The endpoint's own docstring is explicit: it is "not the tbagents bus" but an in-container log so the React shell has rows to render (`:209-214`).
- `management/api/store.py:55-63` stamps the row; both `InMemoryStore` and `PgStore` implement `comms()` and `create_comm()` (`:94-102`, `:192-209`). PostgreSQL persists one JSONB payload per row in `ralph_comms` (`:145-147`).
- `management/frontend/src/App.tsx:13-18` polls comms every eight seconds and `:66-72` renders each row as raw JSON through `ListPanel`.
- `management/frontend/src/components/StatePanel.tsx:51-55` renders only the comm count. It does **not** render comm rows; the rows live in `App.tsx`. `management/frontend/src/api.ts:82-84,148` leaves `AgentComm` untyped and exposes only GET, so there is no send/reply UI.
- The Playwright test proves API POST -> database-backed GET -> visible generic JSON row (`management/e2e/tests/ui-db.spec.ts:73-88`). It proves the surface is alive, not delivery to an agent.

What is missing for a bus:

- no authentication on GET or POST and no binding of `from_handle` to a caller identity;
- no instance identity, channel, correlation/task ID, reply relation, idempotency key, delivery state, expiry, or content-size limit;
- no targeted query, cursor/watermark, ack/read transition, retry/dead-letter policy, or rate limit;
- no subscriber registry or agent heartbeat;
- no relay, notification, SSE/WebSocket stream, or inbox consumer;
- no loop/executor integration. Searches of `runner/loop.py` and `runner/executor.py` find no call to `/api/agent-comms` or `store.create_comm()`.

The PostgreSQL implementation is a useful durable seed, but the API's startup fallback to process-local memory (`management/api/store.py:240-254`) would be unsafe for a real bus if it silently accepted messages during a DB outage. A production comms write must fail closed with `503`, not report durable acceptance from an ephemeral fallback.

There is also no separate typed `Comm` domain/ORM model in these files: `CommIn` is the request validator and `_comm_record()` constructs a dictionary persisted as JSONB. That is sufficient for the demo log, but a real bus needs a versioned envelope and database constraints/indexes for identity, routing, ordering, and idempotency.

### 2.3 The workflow is interpreted but still single-agent per stage

The premise is confirmed, with the precise current scope being prompt-level workflow interpretation.

- The default workflow assigns `decide`, `act`, and `reflect` to `coding_agent` (`workflows/default/v1/workflow.yaml:7-27`). The demonstration research workflow does the same while supplying different prompt templates (`workflows/research-loop/v1/workflow.yaml:12-38`).
- `runner/workflow_behavior.py:1-31` says the behavior seam changes prompt text only. Stage order, schemas, output keys, gates, persistence, and control flow remain loop-owned.
- `Loop` stores exactly one executor as `self.ex` (`runner/loop.py:160-169`). It calls that same executor once for decide, act, and reflect through the selected behavior (`runner/loop.py` around the decide call, `execute_work`, and pending-reflection recovery).
- `runner/executor.py:413-455` implements one subprocess invocation per `run()`; `build()` returns one subscription or API executor (`:579-592`). There is no agent registry, fan-out, multiple concurrent roles, or message polling.

A future workflow that assigns planner, coder, reviewer, and evaluator to distinct live agents needs a scheduler, role/session lifecycle, and an intra-instance bus. The YAML `actor` label alone does not create those processes.

### 2.4 Identity and addressing already available

Useful building blocks exist:

| Existing item | Where | What it can safely mean |
| --- | --- | --- |
| Host-assigned `AQ_INSTANCE_ID` | `host_replica_stack.py:371-380`; `docker-compose.yml:83,138` | Stable instance principal namespace; guest modification policy forbids choosing it |
| Compose project | `host_replica_stack.py:55,281` | Host lifecycle handle, not an authentication credential |
| Parent/requester lineage | request and stand-up records (`replication_request.py:52-54`; `host_replica_stack.py:540`) | Parent-child topology edge after host verification |
| App management address | unique `ports.app_mgmt` and `health_url` (`host_replica_stack.py:530-539`) | Host-to-instance route; never accept a guest-supplied endpoint |
| Instance management API | app service on container `:8090`, published to host loopback (`docker-compose.yml:125-153`) | Natural local mailbox and adapter surface |
| Globally namespaced plans | `runner/loop.py:101-125` | Existing pattern: `<AQ_INSTANCE_ID>/plan/<uuid>` |

Recommended handles should follow the same namespace: `aq:<instance-uuid>:<role>:<session-id>`. Human labels and Compose names are display metadata; authorization keys on the immutable host-assigned instance UUID and authenticated subprincipal.

### 2.5 Ralph/tbagents reference model

Ralph's bus is a valuable reference, not a drop-in trust domain.

The local TalkingBack implementation has:

- a router-wide internal-access dependency (`app/api/agent_comms_router.py:66-68`); it accepts an internal/admin JWT, a service JWT, or a configured `X-Internal-Service-Token` (`app/auth/dependencies.py:301-345`);
- an agent registry with type, capabilities, handle, channel subscriptions, notification URL, metadata, and heartbeat (`app/schemas/agent_comms.py:10-43`);
- durable messages containing channel, sender, structured payload, message type, importance, `in_reply_to`, target handle, expiry, delivery/read state, and timestamps (`app/schemas/agent_comms.py:49-79`; `app/db/db_models.py:2004-2034`);
- `POST /api/agents/messages`, channel reads/search, ID-watermark polling, pending inbox, single-message reads, delivery ack, and read ack (`app/api/agent_comms_router.py:598-929`);
- directed delivery by `target_handle`, broadcasts when the target is null, mentions, and channel subscriptions (`app/services/agent_comms_service.py:195-290`);
- durable-first semantics: HTTP success means stored, while tmux/OpenClaw/Codex/Discord delivery happens afterward and may fail (`app/api/agent_comms_router.py:671-692`);
- push for latency plus cursor polling for coherence, as documented in `docs/agent_comms/inbox_convention.md`.

Lessons AQ should reuse are durable-first acceptance, stable handles, channels plus direct targeting, cursors, idempotency/reply linkage, explicit delivery state, and push plus pull. AQ should not copy Ralph's assumption that tmux can span its participants. It should also improve principal binding: a shared internal service token plus caller-provided `sender_name` is not sufficient isolation between mutually untrusted replicas.

AQ is not currently wired to this service: there is no TalkingBack endpoint/token in integrated Compose or replica stand-up, and AQ's docstring explicitly disclaims being tbagents.

## 3. Ranked use cases

Ranking reflects value to AQ's self-improvement/experiment loop, safety, and how much new authority is introduced.

| Rank | Use case | Why now | Authority posture |
| --- | --- | --- | --- |
| 1 | Host-observed replica health and lifecycle status -> parent/operator | Detect dead, stale, version-skewed, or hibernating experiments; mostly ground truth | Observation only; host derives health |
| 2 | Replica -> parent experiment progress and result package | Makes replicas useful rather than invisible; enables comparison and adoption review | Untrusted claim plus artifact references; parent independently verifies |
| 3 | Parent -> replica work request, clarification, cancel request | Lets the operator/parent steer a bounded experiment | Typed request enters normal local planning/approval path; never direct shell/actuation |
| 4 | Parent adopts a replica result | Closes the experiment loop | Adoption remains parent/host-authoritative and independently tested; a message cannot merge/promote |
| 5 | Intra-workflow planner <-> coder <-> reviewer coordination | Needed when workflows actually instantiate multiple role agents | Local scoped principals; reviewer must be independent of producer where used as a gate |
| 6 | Replica -> replica coordination | Useful for division of experiments and avoiding duplicate work | Routed via host broker with explicit ACL; no direct lateral trust |
| 7 | Third-party A2A interoperability | Valuable once AQ delegates to or serves external agents | Conforming façade and explicit skill-level authorization |

The first two are primarily observability and evidence transport. The latter cases introduce work dispatch and therefore deserve later, separately reviewed slices.

## 4. Proposed logical model

### 4.1 Message envelope

Evolve `ralph_comms` from an opaque demo payload into an append-only envelope, while preserving a compatibility projection for current GET clients:

```json
{
  "id": "01J...",
  "origin_instance_id": "urn:uuid:...",
  "principal_id": "instance:urn:uuid:.../agent:reviewer/session:...",
  "channel": "lineage/urn:uuid:parent/experiments",
  "target": {"instance_id": "urn:uuid:...", "handle": "parent"},
  "kind": "experiment.result",
  "payload": {"summary": "...", "artifact_refs": ["sha256:..."]},
  "correlation_id": "experiment:...",
  "in_reply_to": null,
  "idempotency_key": "...",
  "created_at": "...Z",
  "expires_at": null,
  "trust": "untrusted_claim",
  "delivery": "accepted"
}
```

Rules:

- The server derives `origin_instance_id` and `principal_id` from the authenticated credential. It rejects or ignores claimed identity fields.
- `kind` is an allowlisted enum initially: `health.observed`, `status.report`, `experiment.progress`, `experiment.result`, `work.request`, `work.response`, `receipt`, and `operator.message`.
- Text is one payload field, not the whole protocol. Payload schemas are versioned per kind.
- IDs are globally unique and sortable; consumers use an integer/ULID watermark. `idempotency_key` makes retries at-least-once without duplicate effects.
- Artifact references are immutable digests plus metadata. Do not accept arbitrary host paths or automatically fetch arbitrary URLs.
- `accepted`, `relayed`, `delivered`, `read`, `rejected`, and `expired` are transport states, never evidence that a request was executed or successful.

### 4.2 Addressing and channels

- Instance principal: host-assigned `AQ_INSTANCE_ID`.
- Agent handle: `aq:<instance-id>:<workflow-role>:<session-id>`.
- Direct target: exact instance plus optional handle. Do not authorize on fuzzy prefixes.
- Suggested channels:
  - `instance/<id>/local` for intra-instance broadcasts;
  - `instance/<id>/workflow/<run-id>` for one workflow execution;
  - `lineage/<parent-id>/status` and `lineage/<parent-id>/experiments` for parent-child traffic;
  - `fleet/<operator-defined-name>` only after explicit membership/ACL creation.

Channel membership is host/parent policy. A replica cannot subscribe itself to another replica's private channel merely by naming it.

### 4.3 Two stores, one protocol

Each instance keeps a local durable inbox/outbox in its own database. The parent also maintains the host-visible fleet journal. The relay copies immutable envelopes between them and records transport receipts. This is intentional duplication, not shared database access:

- local agents continue if the host relay is briefly unavailable;
- replicas never receive parent DB credentials;
- the host can audit exactly what crossed a boundary;
- retries are safe through global IDs/idempotency keys;
- teardown of a replica does not erase the parent's experiment audit.

The existing `/api/agent-comms` can remain the compatibility/operator route, but add scoped routes such as:

```text
GET  /api/agent-comms?channel=&target=&after_id=&limit=
POST /api/agent-comms                    # authenticated local/operator publish
GET  /api/agent-comms/outbox?after_id=   # host relay pull
POST /api/agent-comms/inbox              # host relay delivery
POST /api/agent-comms/{id}/ack
GET  /api/agent-comms/stream             # SSE for UI, later
```

`/outbox` and `/inbox` should use separate scoped credentials from operator/UI authorization.

## 5. Transport and deployment options

### Option A: host-authoritative HTTP relay over published management ports — recommended

The host maintains a topology registry produced only by replica stand-up/teardown. It polls each replica's authenticated outbox and health at `127.0.0.1:<app_mgmt_port>` and posts inbound messages to the same address. Container-originated low-latency sends may additionally post to a central host bus at `host.docker.internal`, but correctness does not depend on that callback.

Benefits:

- preserves separate Compose networks and databases;
- reuses the host's real port knowledge and management API;
- host can enforce lineage ACLs, rate limits, replica existence, and teardown;
- supports all four required direction pairs through one HTTP protocol;
- keeps the broker outside the guest import closure, like the replication daemon.

Costs/risks:

- a new host process and host-owned topology/message journal;
- polling latency unless the optional container-to-host push path is enabled;
- must reconcile topology after host/broker restart and distinguish stored from delivered;
- management endpoints need real service authentication before exposure.

Effort: medium. It is the smallest design that preserves AQ's isolation doctrine.

### Option B: shared external Docker control network and central bus service

Attach every instance's app or a dedicated sidecar to a shared external network and run one bus service there. Container-to-container traffic then uses service DNS and container ports.

Benefits: simple addressing, low latency, no host polling, conventional broker topology.

Costs/risks: every attached app gains network-level lateral reachability to the bus and potentially peers; Compose project isolation is weakened; network naming and lifecycle become global host state; a compromised instance has a larger scanning/DoS surface. A dual-homed sidecar with strict egress is safer than attaching the app directly, but adds deployment complexity.

Effort: medium to high. Consider only if fleet size makes host relay polling material. If adopted, peers should talk only to the broker, enforced by network policy/firewall rather than convention.

### Option C: direct parent/peer management API calls

Tell each replica the parent's address at stand-up and let peers call one another's published management endpoints.

Benefits: little infrastructure; natural request/response.

Costs/risks: replicas need routable host-port knowledge; direct peer trust and SSRF risk; changing ports/topology must be distributed; retry/audit semantics fragment; a replica can probe every published management surface. On macOS, a container reaches host-published services through `host.docker.internal`, not the host's `127.0.0.1` literal.

Effort: low initially, high operationally. Do not use as the general topology. A child may be told only the host broker URL and its scoped credential, never arbitrary peer coordinates.

### Option D: share a PostgreSQL mailbox

Give all instances access to a shared append-only mailbox database that they poll.

Benefits: durable ordering, straightforward polling, no push requirement.

Costs/risks: distributing a shared DB credential collapses isolation; SQL permissions and tenant filtering become a critical boundary; one noisy/compromised replica can pressure the central database; schema migration couples all versions. Directly sharing the parent's existing AQ DB is especially inappropriate.

Effort: medium. A central bus service may itself use PostgreSQL, but replicas should reach it through authenticated HTTP, not receive its DSN.

### Option E: adopt TalkingBack/tbagents as the central service

Point AQ adapters at TalkingBack's `/api/agents/messages`, register each instance/agent, and reuse its channels, UI, polling, acknowledgements, and delivery machinery.

Benefits: feature-rich and already operating; strong model for diagnostics and operator workflows; fastest route to cross-tool visibility.

Costs/risks: couples AQ availability and schema to another product; Ralph's tmux relay does not deliver into isolated AQ containers without a new HTTP/poll adapter; the shared internal token model does not provide per-replica principal isolation by itself; TalkingBack's broad routing/agent registry is more capability than the first AQ slice needs.

Effort: low for a demo bridge, medium/high to make the trust boundary correct. Recommend an optional bridge from the AQ host relay into tbagents for operator visibility, not tbagents as AQ's authoritative transport.

## 6. Intra-instance design and tmux viability

Tmux is viable among agents that genuinely share one Linux container and one tmux socket. It is not available in the AQ image today: `container/Dockerfile:12-16` installs certificates, curl, git, openssl, tini, Python, and Codex, but not tmux. The app entrypoint currently starts the status UI, management API, and one loop supervisor directly (`container/app-entrypoint.sh`); there is no tmux server or multi-agent supervisor.

When a workflow first becomes multi-agent, add an instance-local orchestrator that:

1. resolves YAML roles to explicit agent configurations and scopes;
2. creates one session/process per role with bounded concurrency and resource budgets;
3. registers a stable role/session handle;
4. gives each process a local mailbox credential restricted to its workflow/run channels;
5. writes every message to the local durable mailbox before attempting notification;
6. optionally uses tmux `send-keys` or pane signals to wake an interactive agent;
7. requires agents to poll from a cursor on wake, so tmux loss/restart does not lose messages;
8. tears down sessions and expires credentials at workflow completion.

The multi-agent flow can be planner -> coder -> reviewer, but "reviewer" is not automatically an independent safety gate. If its verdict authorizes adoption or consequential action, use a separately configured principal/model/context and verify objective artifacts outside the producer's report. The existing loop-owned gates stay downstream of the conversation.

Use tmux only if interactive persistent panes are actually useful. For non-interactive Codex calls, subprocess workers plus the same durable mailbox may be simpler and more deterministic.

## 7. A2A standard versus a lightweight AQ adapter

### What official A2A provides

As of this review, A2A's latest released specification is **1.0.0**. It is now a Linux Foundation project originally contributed by Google. It defines:

- discovery through `/.well-known/agent-card.json`, including skills, interfaces, capabilities, and security schemes;
- canonical Agent Card, Message, Part, Artifact, and long-running Task models;
- operations such as Send Message, streaming Send Message, Get/List/Cancel Task, subscriptions, and push-notification configuration;
- JSON-RPC, gRPC, and HTTP+JSON bindings rather than JSON-RPC alone;
- standard HTTP authentication declarations including API key, bearer, OAuth/OIDC, and mTLS;
- asynchronous task states and an authorization-required state, while explicitly leaving authorization policy to the implementation.

Sources: [official A2A 1.0 specification](https://a2a-protocol.org/latest/specification/) and [A2A project](https://github.com/a2aproject/A2A).

A2A is point-to-point agent interoperability and task lifecycle. It is not, by itself, a shared topic bus, durable fleet mailbox, operator inbox, or authority model. AQ still needs channels, storage, topology, ACLs, retries, and UI.

### Recommendation

Build a **lightweight AQ adapter first**, with an internal interface deliberately mappable to A2A:

```text
publish(message) -> receipt
poll(principal, cursor, filters) -> messages + cursor
ack(message_id, disposition)
submit_task(skill, message) -> local proposal/task id
get_task(task_id) -> state + artifacts
describe_capabilities() -> skills + auth + endpoint
```

Do not label the early endpoint "A2A compliant." A partial private dialect under `/a2a` would create false interoperability. Keep `/api/agent-comms` as the AQ-native mailbox/operator API. Later, use the official SDK and compatibility tests to expose a separate conforming `/a2a` interface and Agent Card that translate to the same local queues.

Suggested future Agent Card skills are narrow:

- `aq.report-status`
- `aq.submit-experiment-result`
- `aq.request-work`
- `aq.perform-bounded-experiment`

Do **not** advertise `replicate-host`, `run-shell`, `change-capability`, `approve`, or `adopt-result`. A `SendMessage` requesting replication becomes an untrusted proposal to the existing replication path; it never calls Docker or changes a gate.

Full A2A is justified when AQ needs third-party agents, capability discovery, streaming long-running experiments, or interoperable task delegation. For heartbeat/status and parent-child experiment receipts, immediate A2A adoption adds Agent Cards, version negotiation, task persistence, multiple bindings/features, and conformance testing without replacing the mailbox. Estimated effort is roughly twice the first custom mailbox/relay slice and carries specification-evolution risk.

## 8. Security and authority model

### 8.1 Foundational rule

Every replica and agent message is **untrusted input authenticated to a principal**. Authentication establishes who sent bytes; it does not establish that the content is true, safe, authorized, or successful.

The comms plane transports proposals, claims, evidence references, and requests. It grants no capability.

### 8.2 Principal and credential design

- Generate a unique random relay credential per instance at host stand-up, alongside but distinct from existing secrets.
- Bind it server-side to the host-generated `AQ_INSTANCE_ID`, lineage, allowed routes, message kinds, and quotas.
- Derive sender identity from the credential. Do not trust `from_handle`, `sender_name`, `AQ_INSTANCE_ID` in a payload, a Compose label, or a self-published Agent Card.
- Use separate scoped credentials for local workflow agents. A coder cannot impersonate the reviewer, parent, host, or operator.
- Never give a replica the host's global bus-admin token, Docker socket, parent DB DSN, replication override, governance decision token, or another instance's credential.
- Store secrets in generated `0600` files/secret mounts, support rotation and revocation on teardown, and never render them in the UI or logs.
- Prefer TLS/mTLS when the bus crosses a real machine boundary. On the single Mac, loopback/host-gateway HTTP still requires credentials because process/container isolation, not network geography, defines trust.

### 8.3 Authorization matrix

| Principal may send/request | Receiver may do | Never implied |
| --- | --- | --- |
| heartbeat/status | record as claim; compare with host-observed health | healthy, productive, or trustworthy |
| experiment progress/result | store, display, queue independent verification | adopt, merge, promote learning, or mark mission progress |
| artifact digest/reference | fetch only from allowlisted content store; verify digest/type/size | execute artifact or trust its contents |
| work/clarification/cancel request | enqueue typed proposal for local policy | direct shell execution, process kill, or gate bypass |
| replication request | pass through existing validated proposal endpoint | approval, cap change, Docker access, or stand-up |
| review opinion | record as one evidence source | independent verdict unless reviewer independence is proven |

Host/parent authority remains exclusive for replica cap, free-memory gate, Docker execution, port and identity assignment, credential/capability grants, topology membership, operator approval, teardown, and adoption/merge. The parent independently reads ground truth for evaluation; no `succeeded: true` message satisfies a gate.

### 8.4 Safe command intake

There should be no generic `command`, `shell`, or executable payload kind. Parent-to-child traffic uses a closed schema such as `work.request` with a goal, constraints, expiry, correlation ID, and idempotency key. The receiving instance writes it to an inert queue. Its normal scheduler then validates it against mission boundaries, budget, plan/blast-radius gates, approval requirements, and local capability policy.

Even an authenticated parent request is not raw authority to cross the child's gates. Conversely, a replica cannot send a message to the host daemon that changes `AQ_REPLICATION_MAX_REPLICAS`, turns on auto-approval, grants a token, or calls `stand_up_replica_stack()`.

### 8.5 Input and transport hardening

- Per-principal and per-channel rate, burst, total-storage, and concurrent-task quotas.
- Strict maximum envelope/text/attachment sizes; schema versions and `extra=forbid` for consequential kinds.
- Idempotency keys, monotonic cursors, expiry, bounded retry with jitter, and a dead-letter view.
- Plain-text UI rendering, safe link handling, no automatic URL fetch, and no HTML execution.
- Prompt-injection boundary: messages are labelled external/untrusted data and never concatenated into system/developer instructions. Agents receive a structured inbox section with provenance.
- SSRF boundary: callback and peer URLs come only from the host topology registry or authenticated, policy-checked Agent Cards. Reject loopback/cloud-metadata/private-network targets as appropriate; do not let replicas register arbitrary host callbacks.
- Append-only audit for accepted/rejected/relayed messages and credential principal; redact secrets and sensitive payload fields.
- Honest delivery semantics. HTTP 2xx means durable acceptance only. Execution outcomes require a separately correlated result and, where consequential, independent evidence.

### 8.6 A2A-specific preservation

Official A2A requires authentication and makes authorization implementation-specific. AQ's adapter must authorize every operation and scope task/list results to the authenticated instance. Agent Card skills describe callable interfaces, not entitlements. An inbound A2A Task or Message is converted to the same inert, typed queue as AQ-native traffic.

A2A's `TASK_STATE_AUTH_REQUIRED` is a request for authorization, not authorization. The official specification says that transition alone must not authorize an operation. AQ should require an out-of-band, operation-bound host/operator approval token handled by the existing gate, never credentials embedded in agent prose.

## 9. Operator UI

Extend `management/frontend/`; do not create a second AQ console. The parent management UI becomes the fleet view, while each replica may show its own local mailbox.

### 9.1 Message experience

- Live stream through SSE from the parent journal, with cursor polling fallback. The current eight-second polling hook is an acceptable first slice.
- Channel and handle sidebar; filters for instance, role, kind, direction, trust class, delivery state, correlation/task, and time.
- Conversation/thread view using `correlation_id` and `in_reply_to`.
- Composer with explicit target/channel and typed message kind; send/reply for operator messages and safe work requests.
- Visible badges distinguishing `host-observed`, `replica claim`, `agent claim`, `verified result`, and transport state.
- Retry/dead-letter diagnostics and last cursor, without conflating stored/delivered/read/executed.

### 9.2 Topology view

Show the host-authoritative graph/table:

- parent and child instance IDs plus human labels;
- Compose project, lineage, creation time, workflow selector/version, git SHA;
- host-observed `/health`, last successful poll, loop heartbeat, and stale/down state;
- management route as a redacted host-local port, not a clickable arbitrary URL;
- message counts, last inbound/outbound message, pending/dead letters;
- credential status/rotation age, without secret values;
- teardown state and whether the replica still counts against the cap.

Replica-to-replica threads should display the host relay as an explicit hop. This makes the trust and routing model legible.

### 9.3 Consequential UX

Keep conversation separate from authority controls. A message card saying "please replicate" must not contain an implicit one-click bypass. If the operator chooses to create a replication proposal or approve/adopt work, the UI opens the existing dedicated gated flow, shows independently read cap/memory/evidence state, and requires its normal authorization token.

### 9.4 Reuse map

- Keep FastAPI serving the built React app and reuse `usePoll` initially.
- Replace the raw JSON `ListPanel` in `App.tsx:66-72` with a typed `AgentCommsPanel`.
- Extend `api.ts` with a real `AgentComm` type, cursor/filter reads, send, reply, and ack.
- Keep `StatePanel`'s comm count but split local versus fleet/pending/dead-letter counts.
- Add `TopologyPanel`; its source is the host-owned registry, not guest proposal rows.
- Preserve the existing Playwright API-to-UI test and expand it to trust badges, filtering, replies, and topology health.

## 10. Phased build plan

Each phase is independently useful and releasable. Estimates are rough engineering ranges for one experienced contributor, excluding operator soak time.

### Phase 0 — contract, topology truth, and authentication (small/medium, 3-5 days)

1. Create a host-owned replica registry populated transactionally from stand-up and teardown. Include full ports, parent/requester ID, instance ID, project, workflow, SHA, and lifecycle state. Reconcile it against Docker labels and `replica.json` on startup.
2. Define versioned message kinds/envelopes and migrate `ralph_comms` to indexed columns or a validated JSONB envelope.
3. Add per-instance and operator credentials, principal derivation, ACLs, payload limits, idempotency, and fail-closed durable writes.
4. Keep legacy GET response compatibility only as a read projection; deprecate unauthenticated POST.

Tests: schema/contract tests, credential impersonation tests, cross-instance ACL denial, duplicate retry, DB-outage `503`, topology reconciliation, teardown revocation, payload fuzz/size limits, and import-firewall proof that the guest cannot import host relay/replication execution.

Safety review: a reviewer who did not implement the endpoint attacks sender spoofing, shared-token blast radius, fallback-to-memory, SSRF, and whether any message reaches an actuator.

### Phase 1 — one-way host-observed replica status -> parent UI (small, 2-4 days)

The host relay polls known replica `/health` endpoints through `127.0.0.1:<app_mgmt_port>`, writes `health.observed` events to the parent journal, and updates topology health. The UI shows the fleet and live/stale/down transitions. No replica token and no inbound command path are necessary yet.

This is stronger than trusting replica heartbeats: it records the observer, observation time, endpoint, SHA, and failure reason. Replica-authored status can be added alongside it later but cannot overwrite it.

Tests: two fake replicas with distinct ports/IDs, timeout/refusal/version mismatch, restart/reconciliation, monotonic status events, stale threshold, UI reload, and proof that arbitrary proposal-row ports are ignored.

Safety review: verify the poller accepts endpoints only from the host registry, cannot scan arbitrary addresses, has bounded concurrency/timeouts, and cannot call any write/replication endpoint.

### Phase 2 — replica outbox -> parent experiment journal (medium, 4-7 days)

Add authenticated local outbox publishing and host polling. Wire the loop/workflow to emit bounded `status.report`, `experiment.progress`, and `experiment.result` envelopes at lifecycle points. The host copies them to the parent journal with original and relay timestamps. The UI gains threads and artifact references.

Do not let results update mission truth, learning promotion, merge, or adoption. Add a separate verification state owned by the parent.

Tests: at-least-once retries, duplicate suppression, cursor restart, dropped/expired messages, malicious payloads, forged identity, artifact digest mismatch, replica teardown mid-transfer, and an end-to-end two-stack test proving separate DBs/networks.

Safety review: a de-correlated evaluator compares message claims against the replica's real run/database/artifact evidence and proves a forged `experiment.result` cannot change parent authority state.

### Phase 3 — parent/operator -> replica typed work requests (medium/high, 1-2 weeks)

The host relay POSTs an authenticated `work.request` to the replica's loopback management port. The replica stores it in an inert inbox. A separate importer maps allowed fields into the existing local planning queue; all local budget, blast-radius, approval, and capability gates run normally. Add reply/ack UI and cancellation as a request, not a process signal.

Tests: replay, expiry, wrong parent, wrong target, malformed/oversized body, request for forbidden capability, "replicate" and "run shell" injection, cap/approval bypass attempts, crash between store/import, and idempotent re-import.

Safety review: independent red-team test that no message can invoke Docker, mutate replication controls, grant credentials, bypass approval, or be mistaken for measured success.

### Phase 4 — multi-agent workflow runtime and local tmux adapter (high, 1-3 weeks)

Only when a workflow really declares multiple role agents: add role configs, bounded scheduler, scoped local credentials, mailbox polling, process/session health, and cleanup. Install tmux in the image only if persistent interactive panes are selected. Implement tmux wake/delivery behind an interface; keep subprocess delivery as another implementation.

Tests: planner/coder/reviewer happy path, concurrent fan-out bounds, role impersonation, dead pane/restart with mailbox recovery, missed push recovered by cursor poll, session credential expiry, and reviewer independence. Re-run all existing loop gate tests with a malicious planner/coder/reviewer transcript.

Safety review: a separate agent/team reviews the producer-reviewer correlation and proves the multi-agent wrapper cannot move gates out of `Loop` or turn consensus into authority.

### Phase 5 — conforming A2A 1.0 façade and optional tbagents bridge (medium/high, 1-2 weeks)

Use an official A2A SDK to publish a minimal Agent Card and HTTP/JSON or JSON-RPC binding over the same typed queues. Map experiment work to Tasks, updates to Messages/TaskStatus, and outputs to Artifacts. Advertise only implemented skills/capabilities. Run the official inspector/TCK. Separately, an optional host adapter may mirror selected, redacted AQ channels into tbagents for Kevin's cross-system operator view.

Tests: Agent Card/schema/conformance, version negotiation, auth and per-principal task scoping, task replay/cancel, streaming disconnect/recovery if advertised, malicious cards/parts/URLs, and proof that `AUTH_REQUIRED` or task completion does not satisfy AQ approval/adoption gates.

Safety review: protocol/security review independent from functional conformance. Passing an A2A TCK proves interoperability, not AQ authority safety.

## 11. Verification strategy across all phases

Use three de-correlated evidence layers:

1. **Implementation tests:** unit, store, API, relay, and UI tests written with the slice.
2. **Adversarial trust-boundary tests:** authored/reviewed separately and focused on impersonation, replay, injection, SSRF, resource exhaustion, cross-instance reads, and gate bypass.
3. **Ground-truth integration:** real Docker Desktop stacks with separate Compose projects/volumes/networks; inspect Docker labels and DB rows rather than accepting API self-report. Verify parent/replica claims against host-observed health, actual artifacts, and existing gate tables.

Required invariant tests should remain red if any of these become possible:

- a replica or agent changes the replica cap, memory threshold, auto-approval, identity, port, or credential through a message;
- guest-reachable code imports/calls host Docker execution;
- a message or A2A Task directly executes shell/Docker or promotes/adopts a result;
- an agent can forge another principal or read another instance's private channel/task;
- an ephemeral store acknowledges a message as durable;
- loss of tmux loses a durably accepted message;
- a `delivered`, `read`, `completed`, or self-reported `succeeded` field is treated as objective success.

## 12. Final recommendation

Proceed with the two-layer architecture, but define it as **durable local bus + optional internal tmux adapter + host-routed network adapter**. Do not define it as "tmux inside, A2A outside" if that implies tmux itself is the local source of truth or full A2A is required on day one.

The preferred topology is:

```text
planner/coder/reviewer
        | local authenticated HTTP/poll
        v
instance-local durable mailbox <-> optional tmux/subprocess delivery adapter
        |
        | AQ-native adapter now; conforming A2A façade later
        v
host-authoritative relay + topology registry + fleet journal
        |
        +--> parent management API / React operator UI
        +--> another replica's loopback-published management API
        +--> optional redacted TalkingBack/tbagents bridge
```

This arrangement works across macOS host and Linux container boundaries, preserves replica network/DB isolation, reuses the existing FastAPI/Postgres/React seam, and matches AQ's central doctrine: communication carries untrusted proposals and evidence; authority remains with independently checked host/parent gates.

## References

AQ integrated snapshot (`origin/main@22b3208`):

- `ralph_portable/host_replica_stack.py`
- `ralph_portable/replication_request.py`
- `ralph_portable/replication_modifications.py`
- `scripts/host_replication_daemon.py`
- `management/api/app.py`
- `management/api/store.py`
- `management/frontend/src/App.tsx`
- `management/frontend/src/components/StatePanel.tsx`
- `management/frontend/src/api.ts`
- `runner/executor.py`
- `runner/loop.py`
- `runner/workflow_behavior.py`
- `workflows/default/v1/workflow.yaml`
- `workflows/research-loop/v1/workflow.yaml`
- `docker-compose.yml`
- `container/Dockerfile`
- `container/app-entrypoint.sh`

Ralph/TalkingBack local reference (`talkingback-fastapi@c919e447`):

- `app/api/agent_comms_router.py`
- `app/schemas/agent_comms.py`
- `app/services/agent_comms_service.py`
- `app/auth/dependencies.py`
- `app/db/db_models.py`
- `docs/agent_comms/inbox_convention.md`

External primary sources:

- [Agent2Agent 1.0 specification](https://a2a-protocol.org/latest/specification/)
- [A2A project and official SDKs](https://github.com/a2aproject/A2A)
- [Docker Desktop networking](https://docs.docker.com/desktop/features/networking/networking-how-tos/)
- [Docker Compose networking](https://docs.docker.com/compose/how-tos/networking/)
