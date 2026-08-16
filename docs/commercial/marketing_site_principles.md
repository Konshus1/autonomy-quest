# Marketing Site Principles — Checkable Rubric

**Purpose.** This is the spec the fable web-design agent builds against, and the checklist the review verifies against the *built* page. Every principle is a one-line statement plus a concrete **CHECK** a reviewer can PASS/FAIL by looking at (or measuring) the actual rendered page — not by opinion.

**Scope.** A one-page (or tightly-scoped) marketing site for a small, unproven, technical company selling an autonomous-software product (AQ) to founders, small teams, and agencies.

**Brand calibration (load-bearing — governs message hierarchy).** The site LEADS with the **value prop** ("a mission-driven autonomous system that runs your business and does your marketing"), carries **honesty/safety as the TRUST layer**, and uses the **dogfood as PROOF**. Order is **value → trust → proof**, never proof-first or safety-first.

**How to use.** A page PASSES the rubric only if every **[MUST]** principle passes. **[SHOULD]** principles are strongly recommended; a FAIL on a SHOULD must be explicitly justified in the review. Each check is written so two reviewers would agree on PASS/FAIL.

---

## A. Above-the-fold / value-prop clarity

**P1 [MUST] — 5-second comprehension.** A first-time visitor understands *what the product does for them* within 5 seconds of the hero loading.
**CHECK:** Run a 5-second test (show the hero for 5s, hide it, ask "what does this do and who is it for?"). PASS if a naive viewer can restate the core value in their own words. On the built page: the hero headline states an outcome/benefit in plain language, readable without scrolling. (NN/g: users decide within seconds; a clear value prop must land in ≤5–10s.)

**P2 [MUST] — Outcome headline, not category label.** The primary headline names the *result the user gets*, not the product category or the technology.
**CHECK:** Read the H1. PASS if it describes a user outcome ("runs your business and does your marketing"); FAIL if it's a category/tech label ("Autonomous AI platform," "Agentic operator framework") or a vague slogan. (Calendly "Easy scheduling ahead" / Linear "Plan and build products, together" pattern; feature-led headlines are the #1 SaaS conversion killer.)

**P3 [MUST] — One dominant idea above the fold.** The hero communicates exactly ONE core message; no competing value props.
**CHECK:** Count distinct value claims in the above-the-fold region. PASS if there is one headline idea + one supporting subhead; FAIL if two or more co-equal pitches compete.

**P4 [SHOULD] — Supporting subhead adds one concrete detail, <25 words.** The subhead clarifies *how/for whom*, it does not repeat the headline.
**CHECK:** Read the subhead. PASS if ≤25 words and it adds specificity (audience, mechanism, or scope) the headline didn't.

**P5 [MUST] — Reading level is plain.** Hero + primary copy read at roughly 5th–8th-grade level.
**CHECK:** Paste hero+first-section copy into a readability scorer. PASS if grade level ≤9. (Copy at 5th–8th grade converts materially better than advanced-vocabulary copy.)

**P6 [SHOULD] — Hero shows the product or its proof, not abstract art.** The hero visual is a real product view, a live metric, or a concrete demo — not a stock illustration.
**CHECK:** Inspect the hero media. PASS if it depicts the actual product/dashboard/live number or a real demo; FAIL if it's a generic abstract/AI-cliché graphic. (Specific visuals build trust; for AI products, show the real thing.)

---

## B. CTA design

**P7 [MUST] — Exactly one PRIMARY CTA above the fold.** One visually-dominant call to action is visible before any scroll.
**CHECK:** Load the page at 1440×900 and at 390×844 (mobile). Count visually-dominant CTAs above the fold. PASS if exactly one is clearly the most prominent action. (Single-offer pages generate dramatically more leads; multiple co-equal CTAs depress conversion.)

**P8 [MUST] — Primary vs. secondary CTAs are visually ranked.** Any secondary action (docs, GitHub, "how it works") is clearly subordinate to the primary.
**CHECK:** Compare the primary CTA to every other clickable action. PASS if the primary has the highest visual weight (fill, size, contrast) and secondaries are visually quieter (ghost/text link). FAIL if two buttons look equally weighted.

**P9 [MUST] — CTA copy is action + value, not generic.** The primary CTA states the action and what the user gets; no bare "Submit" / "Get started" with nothing attached.
**CHECK:** Read the button label. PASS for action+value ("Start free," "Try it free — no card," "Run your first mission free"); FAIL for "Submit," "Sign up," or bare "Get started." (Action/benefit-driven copy outperforms generic labels by ~30–40% in tests.) **Copy-fit rule:** use "Get started"/"Start free" if there's a genuine free tier (there is — Open/Try); use "Start free trial" only if it's a time-limited trial. Match the word to the real offer.

**P10 [MUST] — CTA has strong contrast and a real affordance.** The primary CTA visibly stands out from the page background and looks clickable.
**CHECK:** Measure the button's contrast against its immediate background (≥3:1 for the shape; ≥4.5:1 for its text). PASS if it stands out and reads as a button. (Contrast/placement move conversion more than button color choice itself.)

**P11 [SHOULD] — CTA repeats down a long page.** On a scrolling one-pager, the same primary action reappears at logical decision points so the user never has to scroll back up.
**CHECK:** Scroll the full page. PASS if the primary CTA (or a sticky variant) recurs at least once mid-page and once near the end, all pointing to the SAME goal. (Mixpanel/Gusto persistent-CTA pattern.)

**P12 [MUST] — One page, one goal (attention ratio ≈ 1:1).** The page drives toward a single conversion goal; distractions and competing exits are minimized.
**CHECK:** Count all links that lead AWAY from the conversion goal in the primary flow (nav items, outbound social, unrelated links). PASS if the marketing flow keeps a near-1:1 ratio of the one conversion action to competing clickable exits; heavy top-nav with many escape links FAILS. (Removing top-nav / extra offers can substantially lift conversion.)

---

## C. Message hierarchy (value → trust → proof)

**P13 [MUST] — Value-first ordering.** The page presents *what it does for me* (benefits/outcomes) BEFORE how-it-works internals or a feature inventory.
**CHECK:** List sections top to bottom. PASS if benefit/outcome content precedes any deep "architecture/how-it-works/feature-list" section. FAIL if the page opens with mechanism or a feature grid.

**P14 [MUST] — Trust layer comes second, framed as reassurance.** Honesty + safety (can't-fake-it, gated authority) appear as the TRUST layer that de-risks the value prop — after value, before/with proof.
**CHECK:** Locate the honesty/safety content. PASS if it's positioned as "here's why you can trust it to do this" AFTER the value prop, not as the opening pitch. FAIL if the page leads with safety/honesty as the headline idea (that buries the value).

**P15 [MUST] — Proof/dogfood is presented as evidence for the claim.** The dogfood (AQ marketing/operating itself, live numbers) appears as concrete proof tied to the value claim.
**CHECK:** Find the proof section. PASS if it shows real, specific evidence (a live metric, a real result, a verifiable demo) explicitly connected to the value prop. FAIL if "proof" is generic adjectives or absent.

**P16 [SHOULD] — Objections handled inline near the decision.** The top buyer objections (why trust autonomy with real actions/money; is it real; what if it fails) are addressed near the CTAs, not buried.
**CHECK:** Near each major CTA, look for objection-handling microcopy or a short FAQ. PASS if the top 2–3 objections are answered where hesitation peaks. (Trust signals belong next to the CTA where anxiety spikes.)

**P17 [SHOULD] — Benefits are quantified where honestly possible.** Outcome claims use concrete specifics over vague superlatives — but only ones AQ can actually stand behind.
**CHECK:** Scan benefit statements. PASS if concrete/specific claims dominate and every number is real; FAIL if copy leans on unverifiable superlatives ("best," "revolutionary"). (Honesty bar: see Section H — no invented stats.)

---

## D. Trust signals for an UNPROVEN small vendor (critical for AQ)

**P18 [MUST] — Credibility without borrowed logos.** Because AQ has no big-name customer logos, trust is built from honesty, transparency, live proof, and safety — not fake or implied endorsements.
**CHECK:** Inventory the trust elements. PASS if credibility rests on genuine signals (live dogfood metrics, open transparency, founder voice, security framing, real early-user counts). FAIL if the page implies customers/scale it doesn't have (e.g., a "trusted by" logo bar of companies that aren't customers). (Early-stage pages must build trust the brand can't yet carry; real small numbers beat vague "trusted by teams everywhere.")

**P19 [MUST] — Live dogfood proof is present and verifiable.** The page surfaces the "it markets and operates itself" proof in a way a skeptic could check.
**CHECK:** Find the dogfood proof. PASS if it shows a real, current, ideally verifiable signal (a live metric, a link to the running instance/its results, a timestamped result). FAIL if it's only a narrative claim with nothing to inspect.

**P20 [MUST] — Honest framing of maturity.** The page does not overstate traction; it's candid that AQ is early, which itself is a trust signal consistent with the "can't fake it" brand.
**CHECK:** Read all traction/credibility claims. PASS if nothing overstates scale, customers, or results beyond what's true. FAIL on any claim the team can't substantiate on request.

**P21 [SHOULD] — Founder voice / why-it-exists.** A direct founder statement (why AQ exists, the honesty stance) is present.
**CHECK:** Look for a named, human founder statement. PASS if present and specific. (For early-stage products, a founder quote/photo outperforms a generic testimonial.)

**P22 [SHOULD] — Security/safety made concrete near trust content.** The gated-authority / least-privilege / "can request but never self-grant" story appears as a concrete reassurance, ideally demonstrable.
**CHECK:** Find the safety section. PASS if it states specific mechanisms (gated by default, human-approved consequential actions, child ≤ parent authority) rather than the word "secure." (Security/safety framing reduces conversion anxiety for autonomy buyers.)

**P23 [SHOULD] — Real early-user / usage signal if any exists.** If there are any real users, pilots, GitHub stars, or waitlist numbers, show the real number.
**CHECK:** PASS if any genuine quantity shown is accurate; FAIL if inflated or fabricated. (Small real numbers > vague claims.)

---

## E. Structure / flow of the one-pager

**P24 [MUST] — Canonical section order.** The page follows a value-first narrative arc.
**CHECK:** Verify order approximates: (1) Hero: outcome headline + primary CTA; (2) What you get / core benefits; (3) Trust layer (honesty + safety); (4) Proof / dogfood; (5) How it works (brief, progressive disclosure); (6) Objections/FAQ + repeated CTA; (7) Final CTA. PASS if value precedes mechanism and trust precedes deep detail. FAIL on feature-grid-first or mechanism-first ordering.

**P25 [MUST] — Scannable, not a wall of text.** Content is chunked with clear headings, short paragraphs, and visual breaks; a visitor can skim the whole value story via headings alone.
**CHECK:** Read only the section headings top to bottom. PASS if the headings alone convey the value → trust → proof story. FAIL if comprehension requires reading body paragraphs.

**P26 [SHOULD] — Progressive disclosure of technical depth.** Deep technical/architecture detail is available but not forced on the first read (collapsible, linked, or lower on the page).
**CHECK:** PASS if the first screenful is benefit-led and technical depth is reachable but deferred (below the fold, expandable, or a linked deep-dive). FAIL if heavy technical detail dominates above the fold.

**P27 [SHOULD] — Length is justified by the goal.** The page is as long as it needs to be to move a skeptical buyer and no longer; every section earns its place.
**CHECK:** For each section, state its conversion job. PASS if every section has a distinct job (inform, prove, reassure, convert); FAIL if any section is filler.

---

## F. Conversion mechanics (forms / friction)

**P28 [MUST] — Minimal signup friction.** The first conversion asks for the least information needed to start; no unnecessary fields.
**CHECK:** Count required fields in the primary signup/CTA flow. PASS if it asks only for what's essential to start (e.g., email only, or SSO); FAIL if it demands name/company/phone before delivering any value. (Trello single-field pattern; fewer fields → higher completion.)

**P29 [MUST] — Single-column form.** Any form is a single vertical column.
**CHECK:** Inspect form layout. PASS if single-column; FAIL if multi-column fields. (Single-column forms complete faster/cleaner.)

**P30 [SHOULD] — Risk-reversal microcopy at the CTA.** The point of conversion states what happens next and removes risk ("free," "no credit card," "sandbox — nothing touches production").
**CHECK:** Read microcopy under/next to the primary CTA. PASS if it names the risk-reversal truthfully. FAIL if absent or if it promises something untrue. (Repeated "no credit card required" is a proven objection-remover — only use claims that are true for AQ.)

**P31 [SHOULD] — Expectation match after the click.** The CTA promise matches what the next step actually delivers (no surprise gate).
**CHECK:** Follow the primary CTA one step. PASS if the destination matches the button's promise (e.g., "Start free" leads to a free start, not a "book a demo" wall). (Baymard: surprise email/gate walls after a CTA break trust.)

---

## G. Design / visual / performance / accessibility

**P32 [MUST] — Clear visual hierarchy.** Size, weight, and spacing make the reading order obvious: headline → subhead → CTA → supporting.
**CHECK:** Squint at the page. PASS if the eye lands on headline then CTA in the intended order; FAIL if elements compete or the CTA doesn't pop.

**P33 [MUST] — Generous whitespace; not cluttered.** Content has breathing room; sections are visually separated.
**CHECK:** PASS if no region feels crammed and sections are clearly delimited; FAIL on dense, edge-to-edge text blocks.

**P34 [MUST] — Mobile-first and unbroken on small screens.** The page is fully usable and visually intact at 390px wide.
**CHECK:** Render at 390×844. PASS if hero, headline, and single primary CTA are visible and legible without horizontal scroll, and tap targets are ≥44px. FAIL on overflow, tiny targets, or a hero that collapses. (Mobile is the majority of traffic; two-column heroes often break on phones.)

**P35 [MUST] — Fast load / Core Web Vitals.** The page loads fast enough not to bleed visitors.
**CHECK:** Run Lighthouse/PageSpeed on the built page. PASS if LCP ≤2.5s (target ≤2.0s), CLS ≤0.1, INP ≤200ms on a mid-tier mobile profile. (A 1→5s mobile load raises bounce probability ~90%; passing CWV correlates with ~24% lower bounce.)

**P36 [MUST] — Typography is legible.** Body text is large enough and set for comfortable reading.
**CHECK:** PASS if body text ≥16px, line length ~50–75 chars, adequate line height; FAIL on tiny or cramped type.

**P37 [MUST] — Accessible color contrast.** Text and interactive elements meet WCAG AA.
**CHECK:** Run a contrast checker. PASS if body text ≥4.5:1, large text ≥3:1, and UI/CTA components ≥3:1 against their background. FAIL on any primary text/CTA below threshold.

**P38 [SHOULD] — Basic accessibility hygiene.** Images have alt text, headings are semantically ordered (one H1, logical H2s), and the primary CTA is keyboard-reachable and focus-visible.
**CHECK:** Inspect DOM + tab through the page. PASS if H1 is unique, heading order is logical, images have alt text, and the CTA is reachable/visible via keyboard. FAIL on missing alt, skipped heading levels, or a non-focusable CTA.

---

## H. HONESTY CONSTRAINT — ethical conversion only (brand-critical)

> AQ's entire positioning is "honest by construction / can't fake it." A manipulative marketing site would directly contradict — and discredit — the product. These are hard gates.

**P39 [MUST] — No fabricated social proof.** No fake testimonials, invented customer logos, made-up user counts, or borrowed credibility the company hasn't earned.
**CHECK:** Verify every proof element traces to something real. FAIL on any testimonial/logo/number that isn't genuine. (Fabricated proof is the cardinal sin for a "can't-fake-it" brand.)

**P40 [MUST] — No fake urgency or false scarcity.** No countdown timers, "only N left," "X people viewing," or expiring-deal pressure that isn't literally true.
**CHECK:** Scan for urgency/scarcity devices. PASS if none, or if any is factually true and verifiable. FAIL on any invented urgency. (NN/g classifies false urgency as one of the worst deceptive patterns — "actively lying to users.")

**P41 [MUST] — No hidden costs or bait-and-switch.** Pricing/terms are honest; "free" means free; nothing consequential is concealed until later.
**CHECK:** PASS if what's offered is delivered as stated and costs aren't hidden; FAIL if "free" hides a card wall or the offer differs from the promise. (Hidden-cost / roach-motel patterns erode trust.)

**P42 [MUST] — No manipulative friction or confirmshaming.** No obstructed opt-outs, no guilt-tripping decline copy, no misdirection steering users away from a legitimate choice.
**CHECK:** Inspect opt-outs, decline links, and toggles. PASS if declining/leaving is as easy as accepting and worded neutrally. FAIL on confirmshaming or obstructed exits.

**P43 [MUST] — Claims are substantiable.** Every capability and outcome claim is one the team could defend with evidence on request.
**CHECK:** For each strong claim, ask "can we prove this today?" PASS only if yes for all. FAIL on any aspirational claim stated as present fact. (This mirrors the product's anti-Goodhart stance — the site must not do what the product refuses to do.)

**P44 [SHOULD] — Honesty as an explicit, visible value.** The site openly states its honesty/transparency stance (turning the constraint into a differentiator).
**CHECK:** PASS if the page explicitly owns "we won't fake results / we tell you the truth" and the site itself visibly lives up to it.

---

## Sources

- Nielsen Norman Group — value proposition, 5-second test, above-the-fold attention (80% of time above the fold): nngroup.com/videos/5-second-usability-test/, nngroup.com (eye-tracking).
- Nielsen Norman Group / Jakob Nielsen — deceptive (dark) patterns; false urgency as "actively lying to users": jakobnielsenphd.substack.com/p/dark-design, deceptive.design.
- CXL — single-offer vs multiple-offer (multiple offers → up to 266% fewer leads); headline clarity & CTA placement > visual polish; distraction removal: cxl.com/blog/how-to-build-a-high-converting-landing-page/, cxl.com/blog/ecommerce-landing-pages/.
- Baymard Institute — post-CTA expectation match / surprise gates; form UX: baymard.com/blog.
- SaaS Research Lab — "Get Started" (freemium) vs "Start Free Trial" (time-limited) usage across 110 SaaS pricing pages: saasresearchlab.com/report/cta-analysis.
- Unbounce Conversion Benchmark Report — SaaS median conversion ~3.8%, top performers 10%+: unbounce.com/conversion-benchmark-report/saas-conversion-rate/.
- Involve.me / Grafit / Foundey / Orbix / Pineable (research roundups citing Invesp, WordStream, ChartMogul) — single CTA (~13.5% vs ~10.5% multi), reading-level effect (11.1% vs 5.3%), testimonials +34%, action-oriented CTA copy +30–40%, social-proof placement above the fold, outcome-first headlines (Calendly/Linear/Stripe), early-stage founder-voice trust, one-field forms (Trello): involve.me/blog/saas-landing-pages, grafit.agency, foundey.com/blog/best-saas-landing-page, orbix.studio, pineable.com.
- Core Web Vitals 2026 — LCP ≤2.0–2.5s / CLS ≤0.1 / INP ≤200ms; 1→5s mobile load raises bounce ~90%; passing CWV ~24% lower bounce; ~1s delay ~7% fewer conversions: corewebvitals.io, digitalapplied.com/blog/page-speed-statistics-2026-revenue-impact.
- WCAG AA contrast (4.5:1 text, 3:1 large/UI), 44px tap targets — accessibility baseline.

---

# Addendum — Domain / Brand Recommendation (evidence-based)

**Question.** For a small technical company selling "a mission-driven autonomous system that runs your business and does your marketing," is a **subdomain** (autonomy.talkingback.ai) sufficient, or does it warrant a **standalone** domain/brand (Kevin owns **agentricity.com**; open to buying better)?

## What the evidence says

- **Standalone root domains carry more trust and memorability.** A custom root domain "inspires far more confidence than a subdomain," is cleaner and more memorable, and communicates professional credibility. A subdomain reads as a sub-section of another brand, not a product in its own right. (bluehost.com/blog/domain-vs-subdomain/, searchenginejournal.com root-vs-subdomain.)
- **SEO authority is siloed by subdomains.** Search engines often treat a subdomain as a separate entity: backlinks earned to `autonomy.talkingback.ai` don't automatically flow up to (or benefit from) the TalkingBack root, and vice-versa. A standalone domain accrues its own authority in one place. Practically, for a *product with its own identity and its own SEO ambitions*, the subdomain neither fully rides the parent's authority nor builds cleanly transferable authority of its own. (searchenginejournal.com, chriscasarez.com/seo-aiseo/how-do-subdomains-affect-seo/.)
- **Comparable SaaS/AI launches favor a standalone brandable .com.** Short, invented/brandable .com names "outsell descriptive domains in SaaS and tech"; .com still dominates trust and recall; memorable names are short (≤3 syllables), easy to spell after hearing once, and distinctive. Notion, Linear, Asana, Stripe, Shopify are all standalone brandable roots — none live on a parent's subdomain. (namesilo.com, nameexperts.com, register.domains.)
- **Counter-weight for AQ specifically:** the subdomain is **free**, and it **leverages the existing TalkingBack brand and the @talkingback_ai audience** — which is the *distribution channel the dogfood already runs through*. A cold standalone domain starts at zero trust and zero SEO. That is a real, non-trivial cost for a tiny team whose entire proof strategy is "watch it market itself on our existing channel."

## Evaluating the specific options

- **autonomy.talkingback.ai (subdomain).** *Pros:* free, instant, rides TalkingBack + @talkingback_ai audience, keeps the dogfood proof on one connected brand surface. *Cons:* reads as a feature of TalkingBack, not a product; siloed/weaker SEO; less memorable; harder to spin out later without a rebrand. **Verdict: fine as a launch/soft-launch home; sub-optimal as the durable commercial brand.**
- **agentricity.com (owned standalone).** *As a name:* "Agentricity" = agent + (electr)icity — evokes "agents as a utility/force." *Pros:* it's a real brandable .com already owned (zero cost, zero risk), sounds like a real word, on-theme (agents/autonomy). *Cons:* ~4 syllables and a slight spell-after-hearing risk ("agentricity" vs "agent-ricity"); it foregrounds "agent" (a crowded, hype-laden category word) rather than the AQ differentiators (honest/safe/runs-your-business); and it's generic to the whole agent space rather than distinctive to AQ's honest-operator wedge. It clears the memorability bar adequately but not outstandingly. **Verdict: a solid, usable standalone that AQ already owns — good enough to build on, not so perfect it forecloses a better buy.**
- **A fresh purchase.** Justified only if it's clearly better than agentricity.com on the memorability criteria (shorter, easier to spell, more distinctive to the honest-operator positioning). Worth a modest spend given a domain is a durable "force multiplier for brand and customer trust," but not worth chasing an exciting-but-weaker name.

## Recommendation (decision-ready)

**Two-track, evidence-supported:**

1. **Now (launch):** point the site at a **standalone root domain**, defaulting to **agentricity.com** since it's already owned, is a legitimate brandable .com, and the evidence favors a standalone root over a subdomain for trust, memorability, and self-owned SEO. **Redirect `autonomy.talkingback.ai` → the standalone**, and keep using **@talkingback_ai for distribution** so you still get the audience leverage while the brand lives on its own domain. This captures the subdomain's one real advantage (existing audience) without accepting its trust/SEO penalty.

2. **Optional upgrade (only if clearly better):** before committing brand assets, spend ~1–2 hours checking whether a **shorter, more distinctive .com** that foregrounds AQ's *honest-operator* wedge (not just "agent") is available at reasonable cost. If yes and it beats "agentricity" on spell-after-hearing + distinctiveness, buy it; if not, ship agentricity.com and stop. Do **not** delay launch waiting for the perfect name.

**Candidate directions to check availability (.com strongly preferred):** names that signal *autonomous operator + honesty/trust + running your business*, kept to ≤3 syllables and easy to spell. Examples to price/check: a coined operator word (e.g., `runwell`, `truerun`, `honestops`-style), an honesty-forward mark, or a "runs-itself" mark. Kevin should check live availability; the selection rule is the rubric above — **shorter, easier to spell, more distinctive to the honest-operator positioning than "agentricity," or don't bother.**

**Honesty-bar note.** The recommendation is *not* "buy an exciting new domain." The evidence says a standalone root beats a subdomain, and Kevin already owns a perfectly serviceable standalone (agentricity.com). The disciplined move is: **use the owned standalone, redirect the subdomain, keep the social audience for distribution, and only upgrade the name if a genuinely better .com is cheaply available.**

### Sources (addendum)
- bluehost.com/blog/domain-vs-subdomain/ and /root-domain-vs-subdomain/ — trust/memorability of root vs subdomain; authority consolidation.
- searchenginejournal.com/root-domain-subdomain-ecommerce-seo/367940/ — search engines treat subdomains as separate; backlink equity siloing.
- chriscasarez.com/seo-aiseo/how-do-subdomains-affect-seo/ — subdomain SEO trade-offs.
- namesilo.com/blog (domain memorability science), nameexperts.com (brandable domains), register.domains (startup TLDs) — brandable .com outperforms descriptive; .com trust dominance; memorability criteria (short, spellable, distinctive).
