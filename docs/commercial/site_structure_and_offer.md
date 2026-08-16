# Site Structure & Offer — Self-Serve, Online-Only (v1)

**Hard founder constraint (decides everything below).** Kevin has a day job and **cannot take sales calls**. This is a **self-serve, online-only** product with **async email support (after hours)**. Therefore: **no book-a-call, no demo request, no high-touch managed sales, anywhere on the site.** Every conversion path must complete without a human on the vendor side in the loop synchronously.

**Governing docs.** Built on `marketing_site_principles.md` (the checkable rubric — principle numbers Pxx below map to it) and the AQ strategy memo. Message hierarchy is fixed: **value → trust → proof** (P13–P15).

**v1 reality.** One-click provisioning is **not built yet**. So the v1 primary conversion is an **email capture / early-access signup**, and Kevin **onboards early signups by email, async, after hours**. The site must be *honest* that this is early access — that candor is on-brand ("can't fake it," P20), not a weakness to hide.

---

## Part 1 — The v1 conversion site structure (single page, one goal = signup)

**One page. One goal: capture an email / early-access signup.** Attention ratio ≈ 1:1 (P12). No top-nav full of escape links; at most a minimal anchor nav that scrolls within the page. No outbound distractions in the primary flow.

**Primary CTA (everywhere it appears):** **"Start free"** → opens/loads the inline email capture. Rationale (P9): there is a genuine free tier (Open/Try), and SaaS convention is that "Start free"/"Get started" signals free, immediate, zero-commitment access — the correct word for a freemium offer, not "Start free trial" (which implies a time-limited trial). "Start free" also honestly matches what the user gets today: they join, and get free sandbox access as it opens. **Secondary CTA:** a quiet **"Questions? Email us"** text link → `mailto:` / async contact. **No call/demo CTA exists.**

> **Honesty note on CTA↔delivery match (P31, P43).** Because full one-click provisioning isn't live, the button must not promise instant product access it can't deliver this second. Two honest options — pick one and keep it consistent:
> - **(A) "Start free"** with microcopy under it: *"Free sandbox — we're onboarding early users in batches; you'll get access by email."* (Keeps the strong action verb; the microcopy makes it truthful.)
> - **(B) "Get early access"** if batching means access isn't immediate for most. Slightly weaker verb, but zero expectation gap.
> **Recommendation: (A) "Start free" + honest microcopy** — strongest action-copy (P9) while staying truthful (P31/P43). Switch the button to a true one-click "Start free" the moment provisioning ships (see Part 3).

### Section-by-section wireframe

**§0 — Minimal header (sticky).**
- Left: wordmark/logo. Right: **one** primary CTA button ("Start free"). Optionally 2–3 in-page anchor links (How it works / Proof / Pricing) that scroll, not leave.
- *Rubric:* P7 (one dominant CTA), P8 (primary visually dominant), P12 (no escape-link nav), P34 (mobile: collapses cleanly, CTA stays visible).

**§1 — Hero (above the fold). THE value prop.**
- **H1 (outcome, not category):** e.g., *"An autonomous operator that runs your business — and does your marketing."* (P2 outcome headline, P3 one idea.)
- **Subhead (<25 words, one concrete detail):** e.g., *"Give it a real goal. It observes, acts on real data, measures the real result — and won't fake its own scoreboard."* (P4, P5 plain language.)
- **Primary CTA:** **"Start free"** + honest microcopy *"Free sandbox · early access by email · no card"* (P9 action+value, P30 truthful risk-reversal, P10 high-contrast button).
- **Hero visual:** the real product — a live mission/goal dashboard or the dogfood's live number — NOT abstract AI art (P6).
- *Rubric:* P1 (5-second comprehension), P13 (value first).

**§2 — What you get (core benefits, outcome-framed).**
- 3 benefit blocks, each a *result for the user*, not a feature:
  1. *"Point it at a goal, it runs the loop"* (observe→decide→act→record→learn on real data).
  2. *"It markets and operates — measured against real outcomes."*
  3. *"It tells you the truth — stalls honestly instead of faking success."*
- *Rubric:* P13 (value before mechanism), P2/P17 (benefits, quantified only where honestly true), P25 (scannable — headings alone carry the story).

**§3 — Trust layer (honesty + safety). The reassurance.**
- Framed as *"why you can trust it to do this"* — comes AFTER value (P14):
  - **Honest by construction / anti-Goodhart:** grades against ground-truth; can't fake its scoreboard; refuses to fabricate. (Answers "how do I know it's not lying?")
  - **Safe by default:** gated authority envelope — high-consequence actions (spend, contact, publish, deploy) escalate to you unless you open the gate; it can *request* but never *self-grant*. (Answers "why trust autonomy with real actions/money?")
  - **Enforced limits on self-improvement:** a child can never silently have wider authority than its parent (host-enforced).
- Concrete mechanisms, not the word "secure" (P22). Ideally a small demonstrable proof (e.g., a screenshot of the agent refusing/escalating).
- *Rubric:* P14 (trust second, as reassurance), P16 (objection handling near decision), P22 (concrete safety).

**§4 — Proof / dogfood. The evidence.**
- *"This site is run by the product."* Show a **real, current, verifiable** signal: a live metric from AQ operating its own marketing (traffic/conversions), timestamped, ideally linking to the running instance or its results.
- Honest framing: show real numbers even when modest; frame as *"watch a real operator work,"* not *"watch it win"* (memo's dogfood-is-double-edged point).
- *Rubric:* P15 (proof tied to claim), P19 (verifiable dogfood), P18 (credibility WITHOUT borrowed logos), P20 (honest maturity), P39 (no fabricated proof).

**§5 — How it works (brief, progressive disclosure).**
- 4-step loop (observe → decide → act → record/learn) + one line on the authority substrate. Deep technical detail is *linked* (docs) or in an expandable, not forced above the fold.
- *Rubric:* P24 (mechanism after value/trust/proof), P26 (progressive disclosure).

**§6 — Editions / pricing (self-serve).**
- Three cards: **Open/Try (free)**, **Full (self-host)**, **Managed cloud** — all with **online signup / self-serve**, no "contact sales." Prices visible (see Part 2). Each card's button = self-serve action ("Start free" / "Get Full" / "Deploy managed").
- *Rubric:* P41 (no hidden costs; "free" means free), P43 (substantiable), P9 (action CTAs).

**§7 — Founder note + honesty stance.**
- Short, named founder statement: why AQ exists, the honesty commitment, and candidly: *"I have a day job — this is self-serve and I support it by email after hours. Early access is onboarded in batches."* Turns the constraint into a trust signal.
- *Rubric:* P21 (founder voice), P44 (honesty as explicit visible value), P20 (honest about maturity).

**§8 — Objections / FAQ (near the final CTA).**
- Answer the top buyer objections inline: *"Why trust an agent with real actions/money?"* → gated authority. *"How do I know it won't fake results?"* → anti-Goodhart. *"Is this real / who's behind it?"* → dogfood + founder. *"What do I get when I sign up today?"* → honest early-access answer. *"How do I get help?"* → async email, after-hours.
- *Rubric:* P16 (objections near decision), P43 (substantiable answers).

**§9 — Final CTA band.**
- Repeat the primary: **"Start free"** + the inline email-capture form (P11 repeat CTA, same single goal).

**§10 — Minimal footer.**
- Email contact (async support), docs, GitHub/OSS link, legal. Kept out of the primary conversion flow so it doesn't dilute attention ratio (P12).

### The signup mechanism (v1)
- **Inline single-field email capture** at hero (§1) and final band (§9). Email only — no name/company/phone (P28 minimal fields, P29 single-column).
- On submit: instant honest confirmation — *"You're on the early-access list. We onboard in batches; watch your inbox."* (P31 expectation match.)
- *Rubric:* P28, P29, P30 (truthful risk-reversal), P31 (post-submit expectation match).

### Honesty gates applied to the whole page (hard MUST)
- **No fake urgency / false scarcity** — no countdown, no "N spots left" unless literally true (P40).
- **No fabricated social proof** — no invented logos/testimonials/user counts (P39).
- **No book-a-call, no demo-gate, no bait-and-switch** — the constraint *and* the brand forbid it (P41, P42).
- **Every claim substantiable on request** (P43); honesty stated as an explicit value (P44).

---

## Part 2 — Self-serve pricing & packaging

**The self-serve + async-support ceiling (the key pricing reality).** Async-only, after-hours email support **caps how high self-serve pricing can go.** Sales-assisted products charge $2k–$5k+/mo *because a human de-risks the purchase and onboards*. AQ can't offer that, so AQ prices where a buyer will **commit online, unassisted**: roughly the self-serve dev/AI-tool band. Compare Devin (self-serve at **$20/mo + $2.25/ACU**, Team $500/mo), Linear/Notion self-serve tiers ($8–20/seat), Vercel/Railway-style usage tiers ($20–$100s/mo self-serve before "contact us"). **Practical self-serve ceiling without a human in the loop: low-to-mid hundreds/mo**; anything above that generally needs sales-assist AQ can't provide. Keep an **honest "email us for larger/custom"** as the *only* escape hatch above the self-serve cap (async, not a call).

### Tier 1 — Open / Try — **Free** (sandbox-limited)
- **What:** free sandbox instance; real loop, but consequential-action gates **cannot be opened** (no real spend/contact/publish/deploy) → safe, zero-risk trial that also protects AQ.
- **Rationale:** non-negotiable top-of-funnel; the RSI/OSS reference points are free (exo), so a paywall-only AQ dies on contact. Free tier = funnel + dogfood credibility + the natural upgrade trigger (you hit the sandbox limit → upgrade to act on the real world).
- **CTA:** "Start free." **v1:** = the email/early-access capture.

### Tier 2 — Full (self-host, licensed) — **~$49–$199/mo** (self-serve, online, monthly; annual discount)
- **What:** licensed self-host; you run it on your own substrate; real-world authority gates you configure; async email support. "You own it" answers the small-vendor-risk objection (memo).
- **Price rationale:** sits in the self-serve dev-tool band — above hobby, below sales-assisted. Suggest **launch at $99/mo** (annual ~$990/yr, ~2 months free) as the anchor; a lighter **$49/mo** solo tier and a **$199/mo** multi-operator tier give a good/better/best ladder without a sales call. Don't chase the $2k+ AI-SDR band — that requires human onboarding AQ can't offer. **Don't underprice to the $1/day "AI employee" race-to-bottom** either; AQ's honesty/safety is a *premium* self-serve position.
- **CTA:** "Get Full" → self-serve checkout (Stripe) + license/download. **v1 bridge:** if checkout isn't wired, this routes into the same early-access capture with an honest "paid self-host opens soon" note.

### Tier 3 — Managed cloud — **~$149–$499/mo** — *only if self-serve-provisioned*
- **What (and the hard condition):** an **auto-deployed cloud instance the user configures themselves**, with async email support. This is **NOT** high-touch managed service. If it can't be self-provisioned (one-click deploy + self-config), **it does not ship in v1** — a human-run managed offering violates the founder constraint.
- **Price rationale:** managed carries infra + convenience premium over self-host, but the async-support ceiling still applies → **$149–$499/mo**, not the $2k–$8k a sales-assisted managed offering would command. Suggest **launch at $249/mo** as the single managed price; add usage-based compute pass-through if the loop is compute-heavy (Devin-style ACU model) so heavy users pay proportionally without a sales negotiation.
- **CTA:** "Deploy managed" → self-serve provisioning. **v1:** almost certainly **early-access/waitlist** (auto-provisioning likely not built) — label it honestly.

### Above the ceiling
- **Only escape hatch:** a quiet *"Need more / custom? Email us"* (async). **No "Contact sales," no call booking.** This honestly serves the rare large buyer without breaking the constraint or the motion.

### Packaging summary
| Tier | Price (launch) | Motion | Support | v1 status |
|---|---|---|---|---|
| Open/Try | Free | Self-serve | Async email | Email/early-access capture |
| Full (self-host) | $99/mo (anchor; $49 solo / $199 multi) | Self-serve online | Async email | Early-access → self-checkout when wired |
| Managed cloud | $249/mo ($149–$499 range) | **Self-provisioned only** | Async email | Waitlist unless auto-deploy exists |

---

## Part 3 — Self-serve onboarding flow (and the product requirement it implies)

### The flow the site promises
**v1 (bridge — honest early access):**
1. Visitor hits "Start free" → single-field email capture (P28/P29).
2. Instant honest confirmation: *"On the early-access list; onboarded in batches."* (P31)
3. Kevin, async/after-hours, sends onboarding email → provisions a sandbox / sends access + a first-goal starter → user runs their first mission.
4. First value = **user sees the loop run and produce an honest, measured result on a real goal** (even a small sandbox one). That "aha" is the conversion to a paid tier.

**Target (true self-serve, post-provisioning):**
1. "Start free" → email/SSO signup (single step).
2. **One-click provisioning** of a sandbox instance (no human in loop).
3. Guided first-goal setup (templated: "pick a goal, connect a data source in sandbox").
4. Loop runs → user sees measured result → upgrade prompt at the sandbox authority boundary (open real-world gates = upgrade to Full/Managed).

### What the product MUST have for true self-serve (flag honestly)
- **One-click provisioning** of an isolated instance (the gap that forces the v1 email bridge).
- **Self-serve authority config UI** — user opens/closes consequential-action gates themselves, safely (the "request but never self-grant," least-privilege substrate must be user-configurable without an engineer).
- **A templated first-goal / onboarding path** so a new user reaches a measured result without hand-holding.
- **Self-serve billing** (Stripe checkout + license/entitlement) with no manual step.
- **Async support surface** — docs + email that genuinely resolve most issues without a call (docs quality is load-bearing when there's no sales/support call).
- **Honest gap to state now:** until one-click provisioning + self-serve authority config exist, "self-serve" is *aspirational*; v1 is *self-serve marketing + async-onboarded early access*. Say so on the site (P20) rather than implying instant provisioning that isn't there (P31/P43).

---

## Part 4 — How this changes with the bounded chat (v2)

When the bounded conversational agent ships, the site's primary CTA can shift from a static email capture to **"Try it now" → an in-page, gated chat** where the visitor gives the agent a real (sandbox) goal and watches the loop run live — turning the hero itself into the demo and the proof (a stronger P1/P6/P15 all at once). Onboarding collapses from "email → async provision" into "chat → instant sandbox → measured result," removing the v1 bridge entirely; pricing can then support a genuine usage-based self-serve upgrade prompt at the moment the user hits an authority gate in-chat. **v1 design leaves room by:** keeping the hero visual slot and the primary-CTA region modular (swap email-capture component → chat component without restructuring), and keeping "early access" language contained to microcopy that's easy to delete once provisioning is live.
