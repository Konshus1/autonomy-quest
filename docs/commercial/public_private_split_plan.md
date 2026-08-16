# Autonomy Quest — public / private split plan
*Draft for review — nothing published. Companion to `aq_capabilities_writeup.md`. Decides exactly what lives in the PUBLIC repo (explainer + minimal sandboxed "try" version) vs. the PRIVATE/commercial repo, and how to keep them similar until (if ever) a hard split is forced.*

## Guiding rules (from Kevin, 2026-08-15)
1. Advanced layer is **private/monetizable** (`autonomy-quest-private`, remote `private`). Public `origin` stays the explainer + a minimal sandboxed version.
2. **Keep public and private as similar as feasible** until a real divergence forces a hard split (may never happen). Develop on private; mirror the shareable/minimal subset to public.
3. **Honesty rule (load-bearing):** the public/minimal version must be safe ON ITS OWN by being sandbox-**limited** (can't spend, reach prod, publish, or truly self-replicate). The paid version is safe by ENFORCED guardrails. Never ship the exciting capability public while withholding the safety — guardrails are a paid *feature*, not a withheld brake.
4. Public posting of the explainer is a **background low-priority** track and every outward post is **drafted for Kevin's review first** (posting is outward/irreversible).

## What the two repos are

### PUBLIC `origin` (Konshus1/autonomy-quest) — "see it, try it, understand it"
- The already-public foundation (unchanged today at 97f4b3f): the mission loop, intent verifier, consequence gate, planning_sufficiency, the aqdb/aqmem skills, comms scaffolding — i.e. the honest-autonomy *idea*, runnable in a sandbox.
- A **minimal, deliberately limited "try" experience**: a mission runs in a throwaway sandbox with the safe defaults, so an evaluator sees the loop plan → act → measure → learn honestly, but it structurally cannot spend, reach prod, publish, or self-replicate for real.
- A plain-language **capabilities/editions explainer** (README + a short page) — what the open version does vs. what Full/Managed add. Content: `aq_capabilities_writeup.md`.
- Purpose: evaluation + top-of-funnel. It should be genuinely useful and genuinely limited.

### PRIVATE `private` (Konshus1/autonomy-quest-private) — the commercial engine
- Everything on public PLUS the advanced/monetizable layer being built now:
  - the **authority/manager-envelope layer** (Slices 1–4 + substrate binding): ground-truth blast ratchet, gated-by-default operator-configurable envelope, substrate binding, request-to-open, replication envelope inheritance.
  - the operational-maturity pieces that make real-scale autonomy trustworthy: enforced-substrate wall, bounded authority-change, safe replication controls, and (later) the deploy + real-world-measure bridges for the end-to-end product.
- Delivery: at-cost licensed download and/or **managed cloud** (we deploy + operate, guardrails configured and maintained).

## The line, concretely (what is public vs private)
| Component | Public (explainer + minimal) | Private (commercial) |
|---|---|---|
| Mission loop, intent verifier, consequence gate, planning_sufficiency | ✓ (already public) | ✓ |
| aqdb / aqmem skills, comms scaffolding | ✓ (already public) | ✓ |
| Minimal sandboxed "try" mission (safe by limitation) | ✓ | ✓ |
| Capabilities/editions explainer | ✓ | ✓ |
| Authority-envelope layer (Slices 1–4 + substrate binding) | ✗ | ✓ |
| Bounded authority-change requests | ✗ | ✓ |
| Safe self-replication controls + envelope inheritance | ✗ (public "try" can't really replicate) | ✓ |
| Enforced-substrate wall + manager/authority ops | ✗ | ✓ |
| Deploy + real-world-measure bridges (end-to-end product) | ✗ | ✓ (built in the test phase) |
| Managed-cloud operation | ✗ | ✓ |

## Keeping them similar (mechanics, until a hard split)
- **One tree, two remotes** (current state): local `main` tracks the private continuation and pushes to `private`; public `origin` stays the pared foundation. The advanced commits simply never go to `origin`.
- **The advanced layer is already cleanly separable** — it lives in dedicated modules (`runner/capability_consequence.py`, `runner/authority_envelope.py`, `runner/substrate_manifest.py`, `runner/authority_request.py`, `runner/replication_authority.py`, `schema/030+`) and is entirely flag-gated (default OFF). So public and private differ by *added private modules + flags-off*, not by divergent edits to shared files — which is exactly what keeps them mirror-able.
- **Mirroring public from private:** when we want to refresh public, cherry-pick/port only the shareable foundation + the minimal-version changes; never the private modules. Because the private layer is additive + inert, public can stay a strict subset with no forks of shared logic.
- **A hard split is only forced** if a private change has to modify a *shared* file in a way that can't stay inert/flag-gated. We haven't hit that; if we do, that file becomes the split boundary and we branch it. Until then, subset-mirror is enough.

## Honesty / safety checklist for the public "try" version (before it ships)
- [ ] Structurally cannot spend money, contact real people, publish externally, deploy, or write outside its sandbox (capability *absent*, not merely gated).
- [ ] Cannot self-replicate outside a toy/disposable scope.
- [ ] The explainer states plainly it is a limited evaluation build and names what the paid editions add — no implication that the free version is production-safe for real autonomous action.
- [ ] No private module or flag that would enable the advanced layer is present in the public tree.

## Sequencing (respects "core container first")
1. Finish + enable the core gated container (private) — in progress; this is the product's spine.
2. THEN the AQ marketing-site end-to-end test (proves plan→build→deploy→measure→learn) — which doubles as the marketing.
3. Public explainer + minimal-version ship as a **background** track, drafted for Kevin's review; not on the critical path to the core.

## Open questions for Kevin (not blocking)
- Domain/brand for the public site + explainer (needed before any outward publish).
- Whether the minimal "try" version is a trimmed build of the same tree or a separate lightweight demo repo (lean: trimmed build of the same tree, tagged, so it stays in sync).
- Pricing/packaging specifics for Full (at-cost download) vs Managed (hosted) — a business decision, deferred.
