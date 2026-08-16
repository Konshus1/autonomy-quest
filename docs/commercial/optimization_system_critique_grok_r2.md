# Round-2 adversarial critique — Grok 4.6 (critic) of optimization_system_plan.md v2

_Actor: Claude. Critic: Grok 4.6, engine-diverse. Verdict: 'Start F-reconcile today; do NOT execute the packaged F+A loop as specified' until an A-entitlement policy + zero-volume DONE exist and H (wrong-product legal + a live honesty contradiction) is treated as an interrupt. Two findings are LIVE-SITE defects, not planning gaps — verified against the artifact and surfaced to Kevin immediately._

## Missed in round 1, still open

Legal pages exist **for the wrong product**. Terms/privacy/refund are TalkingBack / OpenClaw Hosted / credit packs / Life Engine / voice / managed containers (`terms.html` §3–7, `privacy.html` §1–5, 10). The live SKU is a GitHub invite to a private self-host repo. I treated H as “missing pages.” It is “wrong contract, already attached to a live charge.”

Also missed: `index.html` still says “Nothing is charged during early access” on the same page as live $299 / $99 buy buttons. That is an honesty FAIL on the product whose thesis is not faking.

---

## Do the [shipped-truth] flags resolve round-1, or relocate it?

| Claim | True? | What it does |
|---|---|---|
| Offer live at $299 / $99mo | **Yes** (site + Stripe + `stripe_armed_record.md`) | Resolves “no sellable SKU.” Relocates the fork: `site_structure_and_offer.md` still has $99 Full / $249 Managed; the strategy memo still has $200–$1k / $2k–$8k. Checkout is coherent; the planning corpus still isn’t. |
| Legal live | **Pages exist** | Relocates, and **worse**: used to mark H-minimum “largely met” and slide H behind D while Payment Links are already live. |
| Payment Links shipped | **Yes** | Correctly retires “build Checkout Sessions.” Relocates A to entitlement-on-ledger — right move. `client_reference_id` is **not** on the static `buy.stripe.com` hrefs; that hook is aspirational (fine to defer to B, don’t pretend it’s wired). |

---

## Remaining findings (worst first)

1. **§1 / §2 / §3 — H is not met; sequencing it after D while live-charging is the leftover launch hole.** Privacy/terms/refund describe hosted credits and OpenClaw, not Own-it / Stay-current. Graph says H gates A; copy says the gate already passed. **Fix:** H-confirm is an interrupt, not step 3. Rewrite the three pages to this SKU (repo access, GitHub username, no credit packs) before calling the legal class closed.

2. **§3.1 / §4 / `server/app.py` — A-entitlement is specified in a place it cannot live, and the extractor will lie.** Writer is INSERT-only and read-blind; there is no GitHub cred on the box (`stripe_armed_record.md` deferred revoke). Entitlement cannot be grant/revoke code in the webhook. It is a **projection + operator runbook** (SELECT over the ledger → GitHub collaborator add/remove). Current mapping sets `product=own-it` for every `charge` (`app.py` ~379–393), so a Stay-current refund looks like Own-it; `invoice.payment_failed` has no `mode`/`product` at all. Revoke policy is still an open Kevin question (Own-it: entitled until refund only? Stay-current: lapse on cancel / payment_failed?). **Fix:** one page *before* code: policy per SKU + derivation query + test-mode proof that completed / refunded / canceled / payment_failed project to the right state. Do not add `user_access` or a GitHub token to the public container.

3. **§0 / §5 vs §3.1 — first loop has no executable DONE.** Harness demands a named external metric; F-reconcile and A-entitlement don’t have one; paid→active cannot move yet. **Fix:** name the zero-volume DONE for this WIP item: (F) one offer doc = live `index.html` = Stripe prices, other prices marked superseded, and delete “Nothing is charged during early access”; (A) test-mode grant/revoke/refund/cancel projects correctly **and** a real Payment Link row still has fulfillable email + GitHub. Rubric-green without that is still FAIL.

4. **§0 — stage-0 “10 paid→active→retained” is uncountable and fights the founder constraint.** No telemetry from customer self-hosts; invite-accepted ≠ first value; 10 manual onboardings of a self-hosted engine *is* founder heroics. **Fix:** count what you can see (paid + invite accepted + customer-reported first value, or one public dogfood instance). Or admit you need opt-in telemetry. Don’t keep a north star you cannot read.

5. **§5(1) — validity still has no instance.** **Fix:** write three principles for this loop that meet (a)+(b) *before* running actor-critic. If you cannot name one whose FAIL would change the offer or the revoke policy, you are back on rubric-pass.

---

## Verdict

**Start F-reconcile today. Do not execute the packaged F+A loop as specified.**

F-reconcile is a writing job against a known shipped price. That part is sound.

A-entitlement is the right *next* engineering object, but the plan still tells you to build “entitlement logic” without a revoke policy, without a projection, and with a stop rule that cannot fire. Starting that as a harness loop will produce a rubric and maybe another table, not paid→entitled→revoked.

H-wrong-product is live, not “largely met.” It does not block typing the offer doc. It **does** block believing the sequence and treating the legal gate as closed.

Pin the one-page A policy + the zero-volume DONE (finding 2–3), and treat H as an interrupt (finding 1). Then F→A is executable. Until those exist, v2 is a better map, not a start order you can run to done.

[exited with code 0]
