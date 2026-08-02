# DESIGN SPEC — Container/Fleet Registry, Discovery & Inter-Fleet Comms (AQ docker kit)

*New roadmap slice requested by Kevin (2026-08-02, relayed via hrf-1557 / BB #786 batch). Each AQ
docker container must be able to REGISTER with the host and be IDENTIFIED, so containers/fleets can
discover and communicate with each other. Built on the SAME doctrine that Slice 4 (replication)
validated: the **host is authoritative**, the **guest proposes**, and the **guest never gets
docker.sock** — the host mediates everything.*

## 0. Doctrine constraints (non-negotiable, inherited from replication — BB #771 / #189541)
1. **Host-authoritative identity.** A container's identity is MINTED BY THE HOST (extends
   `host_replication_executor.allocate_instance_id`), never self-asserted. A container cannot choose
   or forge its own `member_id`/fleet membership.
2. **Guest never gets docker.sock.** Registration, discovery, and inter-fleet messages are all
   guest→host API calls the host records/mediates. No container talks to the docker daemon; no
   container opens a socket directly to another container. The host is the only broker.
3. **Propose → host-record/mediate**, exactly like `validate_replication_request`: a guest packet is
   validated (forbidden fields rejected: `guest_executes`, `requires_docker_sock`, and now a
   self-asserted `member_id`/`host_token`), then the HOST acts.

## 1. Three capabilities
### (a) Register + Identify
- On boot a container POSTs `/api/fleet/register` `{instance_id?, fleet_id_requested?, capabilities[],
  proposed_labels{}}`. The host:
  - allocates a sanitized `member_id` (reuse `allocate_instance_id`'s traversal-safe sanitizer),
  - assigns/validates `fleet_id` (host policy; a container may REQUEST a fleet but the host decides),
  - mints a **host-signed identity token** = HMAC-SHA256(host_secret, `member_id|fleet_id|nonce`) — the
    container presents this on every later call; the host verifies it. A container cannot forge another's.
  - writes a registry row: `{member_id, fleet_id, instance_id, capabilities, status=active,
    registered_at, last_heartbeat, token_fingerprint}`.
- Heartbeat: `POST /api/fleet/heartbeat` (auth = identity token) updates `last_heartbeat`; a member
  with no heartbeat past a TTL is marked `stale` (not deleted — auditable), mirroring the loop's
  own heartbeat/hibernation honesty.

### (b) Discovery
- `GET /api/fleet/members?fleet_id=<f>` (auth = identity token) returns peers **scoped to the
  caller's own fleet** (a member can only discover its fleet unless the host grants a cross-fleet
  allow-list entry). Returns `{member_id, capabilities, status, last_heartbeat}` — never the token.
- Host is authoritative on membership; a container cannot enumerate the whole host, only its fleet.

### (c) Inter-fleet comms (host-brokered)
- `POST /api/fleet/message` `{to: member_id, payload}` (auth = sender's identity token). The host:
  verifies the sender token, confirms the recipient is a registered member the sender is ALLOWED to
  reach (same fleet, or an allow-listed cross-fleet edge), then appends to the recipient's inbox.
- `GET /api/fleet/inbox` (auth = identity token) drains the caller's pending messages.
- **No direct container↔container channel** — the host is the message broker, exactly as it is the
  replication executor. This reuses the agent-bus delivery pattern but keyed on host-minted
  container identity instead of self-declared handles (closing the self-asserted-handle gap).

## 2. Security properties (what the design must guarantee, each testable)
- **No identity forgery**: a container presenting a token for a `member_id` it wasn't minted cannot
  register-as / discover-as / message-as that member (HMAC verify fails). Test: a swapped token → 401.
- **No cross-fleet leak by default**: discovery + messaging are fleet-scoped; a cross-fleet reach
  requires an explicit host allow-list edge. Test: member of fleet A cannot list/message fleet B.
- **No docker.sock / no host escape**: the guest packet validator rejects `requires_docker_sock`,
  `guest_executes`, AND a self-asserted `member_id`/`host_token` (a container can't claim an identity
  the host didn't mint). Test: each forbidden field → rejected.
- **Traversal-safe ids**: `member_id`/`fleet_id` run through the replication sanitizer (no `..`, no
  path separators) so a registry id can never become a filesystem-write vector.
- **Honest liveness**: `stale` members are labeled, not hidden; the registry never reports a
  dead container as active (the same output-based-liveness bar as the mission loop).

## 3. Build slices (each: build → green → non-Claude review → ship → post, per the roadmap gate)
1. **Registry core** — `ralph_portable/fleet_registry.py`: `mint_identity`, `verify_token`,
   `validate_registration` (forbidden-field guard), an in-memory `FleetRegistryStore`
   (register/heartbeat/members/mark_stale), mirroring the `causal_edge_store` portable-first pattern.
2. **Discovery + comms broker** — store methods `deliver(to, from, payload)` / `inbox(member)`, with
   token + fleet-scope verification; portable + tested.
3. **Management API surface** — `management/api/app.py` routes `/api/fleet/{register,heartbeat,
   members,message,inbox}`, identity-token-authed, off the loop hot path (operator/CI + container
   clients only; the autonomous mission loop does not register fleets — that's infra, not mission).
4. **Container-side client** — a small boot-time `register + heartbeat` client for the docker kit;
   discovery + send/recv helpers. Guest side only ever calls the host API; never touches docker.sock.

## 4. Open contract questions (settle before coding, likely with ccf-kevin-bootstrap + Kevin)
1. `host_secret` provisioning + rotation (where the HMAC key lives; per-host, operator-provisioned at
   deploy, like the replication override).
2. Fleet-membership policy: does a container REQUEST a fleet and the host confirm, or does the host
   assign purely by deploy config? (Spec assumes host-decides, container-requests.)
3. Cross-fleet allow-list shape (who authors it — operator/CI only).
4. Message durability + size caps (bounded inbox, hard-capped payload — reuse the untrusted-content
   discipline from the oracle `reason` cap).
5. Relationship to the existing agent-bus (`/api/agents/messages`): is fleet comms a NEW bus or a
   container-identity-authed view over the same delivery layer? (Prefer reuse of the delivery layer,
   new identity/authz on top.)

## Doctrine cross-refs
Replication (Slice 4): `ralph_portable/replication_request.py`, `host_replication_executor.py` —
host-executes, guest-proposes, no docker.sock, host-allocated sanitized ids. BB #746 (causal),
#754/#772 (publish gate), #771 (replication override). This slice does NOT touch the mission loop's
hot path — registry/discovery/comms are infra surfaces the container + operator use, not principles
the loop acts on.
