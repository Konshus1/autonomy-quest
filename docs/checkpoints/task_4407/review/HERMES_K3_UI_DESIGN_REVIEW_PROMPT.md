# Adversarial UI/UX design review — Task #4407 React management shell (kimi-k3)

You are a **design reviewer** (kimi-k3 via Hermes). Focus on the DESIGN of the React
management console — not code correctness (a separate reviewer owns that). Be adversarial about
design quality: hierarchy, clarity, honesty of state, UX, and accessibility.

## What to review

- Source (your cwd): `/Users/kevincthomas/src/aq-4407-review/management/frontend/`
  - `src/App.tsx`, `src/components/StatePanel.tsx`, `ListPanel.tsx`, `CreateTaskForm.tsx`,
    `ReplicationForm.tsx`, `ManagerMergeForm.tsx`, `src/styles.css`, `src/api.ts`.
- The LIVE rendered UI (served by the OOTB container): `http://localhost:18195/`
  - Fetch the HTML + the hashed JS bundle it references; inspect the DOM/markup it produces.
  - Also see the interim vanilla console fallback: it's the same API, different surface.

## Design questions to attack

1. **State honesty**: the console surfaces `backing` (in_memory_stub vs postgres), `merge_gate`
   (manager_gated), and replication (operator-gated vs AUTO-APPROVE ON). Does the DESIGN make the
   safety posture obvious and hard to misread? Is a dangerous state (AUTO-APPROVE ON) visually loud
   enough? Is a badge the right affordance?
2. **Hierarchy & scanning**: a manager glances at this. Is the most important info (health, backing,
   merge gate, pending replication) where the eye lands first? Are the cards ordered well?
3. **Forms**: create-task / replication-propose / manager-merge. Are the destructive/gated actions
   (replication, merge-decision) visually distinguished from benign ones (create task)? Clear
   labels, error/success feedback, disabled states?
4. **Accessibility**: color-only signaling (ok/warn/err badges) — is there a non-color cue? Contrast?
   Focus states? Labels associated with inputs? Semantic headings/landmarks?
5. **Honesty of the interim vs React surfaces**: does the design ever overstate what's wired
   (e.g., agent-comms rendered as raw JSON)? Is that acceptable or a design debt to flag?

## Deliverable

Ranked design findings (most impactful first): each with the file/component, the design problem,
why it matters to a manager using this, and a concrete improvement. Then:
**`DESIGN VERDICT: APPROVE`** or **`DESIGN VERDICT: REQUEST_REWORK`**.

Report BOTH ways:
1. `bb_create_note` (title `#4407 kimi-k3 UI design review`, tags `task_4407,design_review,kimi_k3`).
2. tbagents bus message to `ccf-4407-ralph-to-aq`:
   `curl -s -X POST http://localhost:8000/api/agents/messages -H 'Content-Type: application/json' -H "X-Internal-Service-Token: $INTERNAL_SERVICE_TOKEN" -d '{"channel":"tbagents","sender_name":"<your-handle>","target_handle":"ccf-4407-ralph-to-aq","message_type":"response","payload":{"text":"DESIGN VERDICT: ... <top findings>"}}'`

Do not modify code. Critique the design; be specific and adversarial.
