# Design: Manager / Authority-Envelope layer (AQ private, monetizable differentiator)

Goal: make autonomous action SAFE AT SCALE without a per-action human, by binding an actor's
capability to an APPROVED WORKFLOW ENVELOPE that is enforced by ground truth, not by the actor's
self-report. This is the paid-edition differentiator. Everything inert/flag-gated (default OFF);
private tree only; red-first + adversarial review before "done".

## The core principle (from Kevin conversation 2026-08-15)
"A claim is not authority" — applied to CONSEQUENCE the way AQ already applies it to the MEASURE.
Today both AQ's execute-to-learn predicate and the consequence gate read the MODEL's self-declared
`blast_radius`. That is the declaration-trust gap: the governed actor writes its own risk label.
Fix: consequence authority derives from GROUND TRUTH (the capability/tool used, the least-privilege
substrate), ratcheted against the model's declaration — never below it. The manager (human or a
higher-perspective process), never the actor about itself, defines the envelope; the actor may only
REQUEST a change.

## Slice 1 (FOUNDATION, building now) — ground-truth blast classification + one-way ratchet
- A capability -> consequence-class map (deterministic, code-owned, NOT model-authored): e.g.
  aqdb writes = internal/reversible; read-only web/query = internal; an email/payment/publish/deploy/
  external-HTTP/delete-prod capability = irreversible_external (blast 3), ALWAYS.
- Effective blast level = max(model_declared_level, capability_implied_level). One-way ratchet:
  the model can raise its own blast, never lower it below what the capability implies.
- Wire into consequence_gate + the execute-to-learn predicate so an UNDER-declared action (model
  tags an external write "reversible") is still gated by its capability. Closes the caveat the
  adversary flagged on execute-to-learn (2932ff9) and the gap Kevin and I discussed.
- Flag-gated (e.g. AQ_GROUND_TRUTH_BLAST), default OFF => byte-identical. Red-first.

## Slice 2 — Workflow capability envelope (declared by workflow, enforced by substrate)
- A workflow/instance declares its envelope: the set of capability classes that are auto-actionable
  vs. escalate-to-human. The envelope is APPROVED at the workflow level (few manager decisions),
  actor runs fast inside it (no per-action human).
- Enforcement is substrate-first: the runtime for a workflow only HAS the capabilities in its
  envelope (least-privilege role/tools), so an over-eager actor physically cannot exceed it.
  The declared envelope is the policy; the substrate is the wall. Gate = backstop.

## Slice 3 — Bounded authority-change REQUEST (actor proposes, manager/human disposes)
- An actor that hits an envelope edge emits a bounded, typed REQUEST to widen (what capability, why,
  scope, reversibility, bound). It CANNOT self-grant. A higher authority (manager role via the
  existing event-driven governance service, or human) authorizes/denies. Default for a NEW workflow
  candidate: analyze + inform the operator of implications (Kevin's simplification).
- Event-driven (fires at edges/gates), NOT a constant poller — cheap, matches Ralph/V2 manager role.

## Slice 4 — Replication envelope inheritance (the compounding-autonomy safeguard)
- The replication REQUEST states exactly what is granted to the child, INCLUDING whether the child
  may widen its own envelope. Default: child envelope <= parent envelope. A wider grant (or the
  right to self-widen) must be EXPLICITLY stated in the request and authorized by a higher authority
  (host review — AQ's default-on rule; relaxed only for demo speed). "Full authority" = full within
  the stated finite grant; the one thing not silently delegable is the authority to widen the grant.

## Contract-alignment (serves the Ralph V2 port cheaply)
Shape the typed verdict/receipt outputs of slices 1-3 to match V2's WorkContract/AuthorityPolicy
verdict JSON (BB #2716): {verdict, proposed_action_digest, reversible, internally_authorized,
low_consequence, formally_entails_progress, live_ground_truth_refs, reasons}. Then Ralph V2 port
step 1 ("freeze/version the contracts") is a byproduct, not separate work.

## Guardrails
Private tree; inert/flag-gated default OFF; red-first; de-correlated adversarial review before done;
NO live-loop behavior change and NO flag flip without Kevin; the eventual V2 cutover is his decision.
