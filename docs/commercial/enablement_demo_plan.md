# Enablement demonstration plan — authority layer live on the DEMO container
*The finish-line milestone: prove the gating actually OPERATES (not just landed inert). DEMO stack only
(aq-demo-parent) — NEVER the live seed aq-seed-c2f4834. Runs once Slice 4 lands. Evidence -> email Kevin.*

## Flags to enable (in the demo compose override ~/.local/state/autonomy-quest/demo-parent/demo.yml,
##   under services.app.environment) — all five, so the whole layer operates:
```yaml
      AQ_GROUND_TRUTH_BLAST: "1"          # Slice 1: one-way blast ratchet
      AQ_AUTHORITY_ENVELOPE: "1"          # Slice 2: gated-by-default envelope (the master switch)
      AQ_SUBSTRATE_BINDING: "1"           # substrate: class bound to aq_actor grants + tool manifest
      AQ_AUTHORITY_REQUESTS: "1"          # Slice 3: actor emits request-to-open (never self-grants)
      AQ_REPLICATION_AUTHORITY_ENVELOPE: "1"   # Slice 4: replica envelope inheritance (confirm exact name at land)
```
(Optional: AQ_SUBSTRATE_TOOLS to override the image tool manifest; not needed for the default demo.)

## Default envelope (no config = gated-by-default) — demonstrate, then show an operator OPEN
The mission's instance.yaml needs NO `authority_envelope:` section to be safe (defaults: the 7
high-consequence classes GATED, internal_reversible OPEN). For step 2 we ADD one line to open a gate.

## Demonstration steps + expected evidence

### 1. An action is correctly GATED (categorical, gated-by-default)
- Give the demo a mission/plan step that trips a high-consequence class at model-declared blast 0 —
  e.g. a step with `touches_human`/`spends_money` true (contact_human/spend_money), or an action text
  the keyword map classes as publish/deploy.
- EXPECT: `human_gate_reasons` returns a categorical gate reason (e.g. "work.touches_human=True ->
  contact_human [declaration-channel binding]"), the work PARKS `awaiting_human` even though its numeric
  blast is 0, AND (Slice 3) exactly one `authority_change_request` row is emitted for that class.
- EVIDENCE: the log gate line + the parked work row + `aqdb`/psql `select * from authority_change_request`
  showing the pending request. THIS is "gated by default, regardless of the model's numeric label."

### 2. The operator OPENS one gate (easily configurable)
- Add to demo-instance.yaml: `authority_envelope:\n  spend_money: open` (open ONE class), rebuild/reload.
- Re-run the same spend_money action.
- EXPECT: the categorical gate for spend_money no longer fires; the action falls through to the NUMERIC
  gate (so a low-blast spend auto-proceeds; a high-blast one still gates numerically — opening never
  disables the numeric backstop). Other classes stay gated.
- EVIDENCE: same action now not categorically gated (log + work not parked on that class). THIS is
  "operator opens whatever gates it wants, easily configurable; actor never self-opens."

### 3. A replica INHERITS a narrowed envelope (Slice 4 — compounding safeguard)
- Parent instance runs with an envelope that GATES a class (default). Trigger a replication whose request
  tries to OPEN that class WITHOUT the host widen token.
- EXPECT: the child's effective envelope keeps that class GATED (child effective = authenticated-parent
  ∩ request; unauthorized widen stripped). A host-authorized widen (valid AQ_REPLICATION_WIDEN_TOKEN)
  DOES open it.
- EVIDENCE: the child's written `authority.yml` overlay / its /health or config showing the class GATED
  despite the request; the host log line stripping the unauthorized widen. THIS is "a child can never
  silently be wider than its authenticated parent."

## Guardrails for the run
- Only aq-demo-parent + replicas it spawns. Echo the target + confirm "demo" before any compose/volume op.
- Never touch aq-seed-c2f4834. Controlled replica stand-up only (cap-bounded), teardown after.
- This is reversible/demo — within granted authority. Enabling on the LIVE loop is a SEPARATE Kevin decision.

## Deliverable
Capture the three evidence blocks -> email Kevin (routed) the demonstrated milestone: "core container with
all the gating OPERATING — here's an action gated by default, an operator opening one gate, and a replica
inheriting a narrowed envelope." Leave the demo stack up for his inspection.
