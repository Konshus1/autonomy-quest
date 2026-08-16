# Agentricity optimization system — meta-planning (task #6095) — v2

**Status:** research/planning for Kevin's review. Nothing here builds, deploys, spends, contacts a
customer, or changes pricing — all Kevin-gated. This is the "do first" meta-planning output: the loop
set, dependency graph, sequencing, and the shared harness every loop runs on.

**v2 — after actor-critic round 1.** Actor: Claude (c2f-4834). Critic: Grok 4.6 (headless, engine-diverse).
Grok's verbatim critique is in `optimization_system_critique_grok_r1.md`; it found real, load-bearing
flaws and this revision incorporates them. Changelog at the end. Two of Grok's premises were stale (it
read the planning docs, not the shipped state) — corrected inline and flagged as **[shipped-truth]**.

---

## 0. Stage-0 objective, WIP limit, and the founder constraint (new — the plan lacked a north star)

The system optimizes toward one near-term objective, not eight loops in the abstract:

> **Stage-0 win condition: get 10 strangers from *paid → actually running the product to first value →
> still there a month later*, and stand up one public, honest proof of the product working — without the
> process consuming the founder.**

Rules that keep this real:
- **WIP = 1.** One loop in flight at a time. Eight loops × five steps × two engines × a single human
  gate is a recipe for motion without progress. Finish-and-gate before starting the next.
- **Founder-hours are on the critical path.** Kevin is currently the only person who can ship AND the
  after-hours onboarder. Any loop that adds founder toil (manual fulfillment, support, review load) is
  measured in *his hours*, and a loop that would only survive on founder heroics is not done.
- **Stop rule.** A loop is done when a **named external metric moves** (paid→active count, time-to-first-
  value, organic sessions, refund rate), not when a checklist is green. See the harness (§5).

## 1. The frame — and what's already shipped

Eight loops, one system: **get** customers, **convert**, **deliver value**, **keep**, **learn**, **prove**,
**run soundly**, **stay legal**. Kevin's two ("manage" / "maximize" the money) are the run and the
get/convert loops; the other six make those compound instead of leak.

**[shipped-truth] The offer, legal surface, and checkout already exist** (they are not open problems):
the offer is **$299 one-time "Own it" + $99/mo "Stay current"**, live on the site and in Stripe
(decision #968); checkout is **Payment Links + a read-blind webhook ledger** (#968/#969); privacy/terms/
refund pages are live at their app-store paths. So the early work is not "invent the offer/legal" — it is
**reconcile, prove, and de-risk** what shipped (see §3).

| | Loop | One-line job | Task |
|---|---|---|---|
| A | Manage money in | entitlement ledger, profitability/P&L, support | #6092 (research #6093) |
| B | Maximize money in | marketing tracking + attribution, SEO, conversion | #6096 |
| C | Product quality & continuous vetting | the self-hosted engine kept proven + improving | #6097 |
| D | Retention & customer success | onboarding → **first value** → retention → churn | #6098 |
| E | Feedback → product/positioning | voice-of-customer meta-loop | #6099 |
| F | Offer / pricing / positioning | the best overall business model | #6100 |
| G | Trust / proof / credibility | display real, re-derivable proof | #6101 |
| H | Compliance / legal / risk | privacy, Terms/Refund, tax, security, PII | #6102 |

## 2. Dependency graph (v2 — corrected: diagram now matches prose; missing edges added)

Round-1 caught that my first graph disagreed with its own prose, left E with no inbound, and mislabeled
E/A as "upstream." Corrected — customers and cash sit in the middle; nothing is purely upstream:

```
              F (offer/pricing) ──prereq──► B (acquisition: mktg+SEO+conversion)
              ▲   ▲                              │  ▲
        feeds │   │ feeds                  sells │  │ trust is a
              │   └────────── C (product) ───────┘  │ conversion input
   E (voice)──┤        (you can only market/         │
        ▲     │         price what it DOES)          ▼
        │     └───────────────► D (retention/CS) ◄── B  (acquired users to keep)
        │                          │   │
        │   ┌──────────────────────┘   └──► A (money mgmt: entitlement, P&L, support)
        └───┴─── A + D feed E (support IS voice-of-customer + CS)
                                     │
        C + D + A produce real numbers ─► G (trust/proof) ──► (back into B as conversion input)

   H (legal/risk) = a GATE across B and A: no capture/charge starts until H-minimum holds.
```

- **F → B (prerequisite, not sibling):** B instruments a funnel whose destination is the offer; a funnel
  to an undefined offer measures nothing. **[shipped-truth]** the offer exists, so F's *near-term* job is
  reconcile the three forked planning docs to the shipped $299/$99mo truth (see §3), not invent price.
- **C → B and C → F:** you can only market and price what the product actually does/delivers.
- **B → G and G → B:** dogfood traffic/conversion *is* proof (feeds G); trust is a conversion input (feeds B).
- **B → A:** no money to manage until acquisition produces some.
- **A → D/E, D → E:** support *is* customer success and the richest voice-of-customer channel.
- **H is a gate, not a vibe:** collecting emails is PII and charging cards needs ToS/refund/tax posture;
  B and A cannot *legally* run past a minimum. **[shipped-truth]** privacy/terms/refund already ship, so
  H-minimum is largely met; the open H item is the **Stripe Tax decision** + confirming PII handling.

## 3. Sequencing (v2 — reordered: offer/fulfillment/legal before traffic; SEO is not loop 1)

Round-1's decisive correction: **SEO-first pours strangers into a funnel whose end-state isn't proven.**
The bind at this stage is not crawlability — it is whether a paying stranger can actually reach first
value, and whether we can even see the money soundly. Revised order:

1. **F-reconcile + A-entitlement ledger (the money is *sound*).** Reconcile the forked offer docs to the
   shipped $299/$99mo truth (one written offer). Confirm the webhook ledger records **entitlement**
   correctly across the events that matter — `checkout.session.completed`, refund, `invoice.payment_failed`,
   subscription cancel — i.e. paid→entitled→(later)revoked, not just "a row landed." **[shipped-truth]**
   the append-only ledger exists; the gap is the *entitlement/revoke* semantics on top of it.
2. **D-fulfillment-to-first-value (the delivery actually works).** Prove a stranger goes **paid → invited →
   self-hosted → first real value**, and measure **time-to-first-value**. Today fulfillment is Kevin
   emailing a GitHub invite after hours; the unproven part is everything after the invite. This is the
   loop most likely to be silently broken, and the one that makes "don't leak the few you get" possible.
3. **H-confirm (min legal holds for money + PII).** Confirm privacy/terms/refund cover paid checkout +
   signup PII, and make the **Stripe Tax** decision. Mostly a verification pass given what's shipped.
4. **G-honest-demo (the one proof asset).** Stand up the public, honest dogfood/refusal-to-fake demo the
   strategy memo already calls the win condition — the single strongest trust + conversion asset. Wire the
   site's empty proof-metric slots to **real** numbers (or leave them empty).
5. **B-acquisition (now pour traffic into a closed, proven funnel).** Marketing attribution
   (visit→signup→purchase, extend `analytics_events`), then SEO. **Technical SEO is a half-day checklist
   inside B** (robots/sitemap/canonical/OG/JSON-LD — see `seo_audit.md`), not the first loop; content/
   keyword strategy is the ongoing part.
6. **F-experiments, C-improvement cadence, E-analysis, deeper A/H** — continuous, once there's volume and
   signal to learn from.

**This order is volume-derived and must be re-derived from live numbers, not frozen.** The instant real
customers exist, D and A jump (a lost customer at low volume is proportionally catastrophic).

## 4. Build-vs-buy (v2 — corrected: Stripe is not as free as I claimed)

Round-1 corrected three factual errors. Revised:
- **Stripe Sigma is a PAID add-on** (~$10–15/mo at low volume, after a trial), not free. Buy it only when
  SQL over Stripe-only data is the actual bottleneck — not at near-zero volume.
- **Stripe does NOT give visit→signup funnel data.** That attribution is ours to build (tie
  `analytics_events` to purchases via the Payment Link's `client_reference_id`/metadata — **[shipped-truth]**
  we use Payment Links, so metadata is the right hook; the old plan doc's "Checkout Sessions" is stale).
- **The real A-build is the entitlement path**, not a dashboard: grant on
  `checkout.session.completed`, **revoke** on refund / `invoice.payment_failed` / cancel — the ledger is
  the substrate, entitlement is the logic on top. The GitHub invite is a *fulfillment step after* that.
- **Buy / enable, don't build:** Stripe **Customer Billing Portal** (cancel / update card — do not build a
  UI), receipts, **Radar**, Smart Retries. **Stripe Tax** is a decision (and an H item), not free.
- **COGS is not just Azure + Stripe fees.** For an AI product the real variable cost is likely
  **model/inference spend** — it must be in the margin join or profitability is fiction.

Net must-build: attribution glue, the entitlement/revoke logic + fulfillment tracking, the
profitability join (revenue − Stripe fees − Azure − **LLM/inference**), and the honest proof surface.

## 5. The shared actor-critic / rubric harness (v2 — rebuilt: validity, non-waivable classes, real independence)

Round-1's deepest catch: my harness optimized **rubric-pass**, which is Goodhart bait — and this product
is an *anti-Goodhart* product (its whole thesis is that verification, not output, is the value; see the
repo's intent-verifier / mission_delta work). A checklist-green harness on a fake-proof-averse product
would ship confident theater. Rebuilt with four hard changes:

**(1) A principle must be VALID, not just checkable.** A principle is admitted only if BOTH:
  (a) two independent reviewers score it the same on a *named artifact* (reliability), **and**
  (b) a FAIL would change a real decision (validity). "`<title>` ≤ 60 chars" is reliable but worthless
  unless a failing title would actually change what we ship. **Do not import the existing 44-principle
  `marketing_site_principles.md` as the exemplar** — round-1 correctly noted several of its items
  ("five-second naive viewer," "visual weight," "near-1:1," "squint test") are *not* checkable and would
  be rubber-stamped by two LLMs. Rewrite principles to this bar; folklore is not a validity source.

**(2) The loop is done when a NAMED EXTERNAL METRIC moves — not when the checklist is green.** Each loop
declares one external, re-derivable outcome up front (paid→active, time-to-first-value, organic sessions
via Search Console, refund rate, a live checkout that succeeds). Rubric-pass without that movement is an
explicit FAIL, not a done.

**(3) Non-waivable classes.** "Kevin accepts the failure" is bounded, not a universal escape hatch.
**Honesty (proof numbers), legal/PII/tax, fulfillment-to-first-value, and payment/entitlement correctness
are non-waivable** — they cannot be shipped as an accepted FAIL. Everything else Kevin may waive only by
naming the *class* and the reason. And **the actor cannot be the sole rubric author** (self-authored +
self-scored + self-waived is no test); critic-found gaps become blocking principles unless Kevin waives
the class.

**(4) Real independence, with a non-LLM oracle.** Claude-actor / Grok-critic is *weak* de-correlation:
both are LLMs with overlapping pretraining, the same SaaS folklore, the same taste for complete-looking
checklists, and no ability to feel a refund or a churn. So:
  - the critic **scores blind** (does not see the actor's self-score first), to avoid anchoring;
  - **at least one principle per loop is settled by a non-LLM instrument** — Lighthouse (perf/SEO), a
    Stripe export (revenue/refunds), a real **human 5-second test**, an actual **live checkout**, Search
    Console (organic) — never two models agreeing;
  - the critic must cite the artifact (file/line/URL/number) per principle, and **evidence is spot-checked**
    (a fabricated `file:line` must not sail through — the same bind-to-artifact rule that caught the
    multi-secret stale image tonight).

**Honesty invariant (hard, from #6101):** any number shown as proof must be real and re-derivable; the
site's empty proof-metric slots stay empty until they read true. *Verification is the product, so faking
the proof of verification is the worst possible lie.*

**Kevin-gate:** research, rubrics, critiques, plans — autonomous. Every live build/deploy, Stripe/ledger
write, refund/complaint, customer contact, marketing spend, and pricing change — Kevin's.

## 6. Changelog — what actor-critic round 1 changed

- **Added §0** (stage-0 objective, WIP=1, founder-hours on the critical path) — the plan had no north star.
- **Rebuilt §5 harness**: validity (not just reliability), external-metric completion, non-waivable
  classes, actor≠sole author, blind critic + a **non-LLM oracle per loop**; stopped holding the 44-principle
  file up as the exemplar (it fails the checkable bar).
- **Reordered §3**: offer-reconcile + entitlement-soundness + fulfillment-to-first-value + honest-demo
  **before** traffic/SEO; SEO demoted to a half-day checklist inside B.
- **Redrew §2**: diagram now matches prose; added F→B, C→B/F, B→G, G→B, B→A, A→D/E, D→E; H marked as a
  gate; dropped the false "E, C, A are upstream" claim.
- **Corrected §4**: Sigma is paid; Stripe gives no funnel; the entitlement/revoke path is the real build;
  Billing Portal/Tax are buy/decide; **LLM/inference is the real COGS**.
- **Flagged [shipped-truth]** where Grok read stale planning docs: the offer, legal pages, and Payment-Link
  checkout already ship — so the early loops *reconcile and prove*, they don't *invent*.

**Open for round 2 / Kevin:** does the revised sequence match Kevin's read of the bottleneck? Is stage-0
(10 paid→active + one honest demo) the right win condition? The forked offer docs
(`site_structure_and_offer.md`, `marketing_strategy_memo.md`) need reconciling to the shipped $299/$99mo
truth — that's a concrete F-loop first task.
