# AQ Marketing Strategy Memo (commissioned research, 2026-08-15)

Skeptical outside research. See task #6087 / BB #2740. Full sources at bottom.

## THE EXO FINDING
- The RSI "exo" Kevin heard of = **exoharness/exo** (github.com/exoharness/exo, exoharness.ai) — NOT exo-labs (distributed inference). Open-source (MIT) Rust+TS "fully recursive self-improving agent harness"; ~680 stars; API "early/unstable". Team: Alex Krentsel + Ankur Goyal (Braintrust founder); publicly championed by a16z GP Martín Casado (7/25). Credible, a16z-adjacent, OSS momentum.
- Architecture overlaps AQ: (1) executor (replaceable), (2) exoharness = durable state incl. IMMUTABLE append-only event log ("only part the agent cannot modify"), sealed secrets, (3) sandbox. Supports clone/lineage/rewind.
- OVERLAP AQ must answer: exo's immutable log ≈ "can't fake scoreboard"; exo clone/lineage ≈ "child ≤ parent". BUT exo guarantees AUDITABILITY/rollback, NOT honest grading vs external BUSINESS outcomes, NOT a gated authority envelope for real-world consequential actions (spend/contact/publish/deploy) with host-enforced least-privilege + cryptographic child≤parent authority. Different problems.
- VERDICT: exo is pointed at research-y recursion (demos: games, cost-opt, systems), NOT running a business. It VALIDATES Kevin's thesis ("practical+honest+safe, not AGI/RSI"). Risk: exo's OSS + a16z distribution could ADD a business-authority/measurement layer later. AQ's moat must be the DEMONSTRATED dogfooded business outcome, not the architecture (partly commoditized).
- Other RSI players: **Recursive** ($650M @ $4.65B, Socher/Rocktäschel, nothing public) — pure frontier lab, sidestep. **Weco AIDE²** — academic RSI benchmark results. RSI lane = hot, overcapitalized, pointed AWAY from "run a business honestly" = white space for AQ.

## MARKET REALITY-CHECK (the key one)
- The "autonomous AI employee/SDR" narrative PEAKED 2024-25 and BROKE in 2026: Artisan "Ava"/11x produced bot-spam outreach, LinkedIn restricted automation, buyers REVERTED to "AI drafts, human sends". AQ's "run a business autonomously" will trigger EXACTLY this skepticism — but AQ's honest+gated framing is the well-timed ANTIDOTE, IF it doesn't sound like the same 2024 promise.
- Autonomy in production is real but VERTICAL + BOUNDED (Devin/Cognition: $492M ARR, named enterprises, $26B val). Market believes autonomy when MEASURABLE + SCOPED, not "runs your whole business".
- "Guardrails/approvals/human-in-loop/audit" = now STANDARD vocabulary → AQ's "safe by default" is TABLE STAKES as marketing (though AQ's implementation is more rigorous than most copy).
- REWARD HACKING / Goodhart = documented, hot, UNSOLVED (SpecBench, EvilGenie: models reward-hack up to 100%, only 70-95% deterred even when told not to). AQ's "honest by construction" targets a problem the field ADMITS it can't solve = strongest, most under-appreciated wedge.

## DIFFERENTIATION (candid)
- "Honest/anti-Goodhart" = MOST defensible + timely, ONLY if DEMONSTRATED (reproducible demo: agent stalls/refuses to fake a green scoreboard vs external ground truth). Most ownable AND most fatal to overclaim (one gameable seam breaks the whole brand).
- "Safe by default" gate = real engineering, but table-stakes positioning. Lead with the consequence ("it physically cannot spend your money/email your customer without the gate you opened"), not the word "safe".
- "Child ≤ parent, host-enforced" = novel but niche (security buyers); great proof point, bad headline.
- "Real-world measurement/dogfooding" = the credibility ENGINE + only uncopyable moat. Invest disproportionately. Riskiest: honesty forces public admission if it underperforms.
- WEAK/UNPROVEN: no shipped external proof yet (vs Devin's ARR/logos, exo's stars/a16z); architecture partly commoditized; "runs a business" is the most-burned phrase; small team vs giants.

## ICP / BEACHHEAD (would-PAY, not would-impress)
1. **Solo founders / tiny teams / indie SaaS + agencies** needing growth/ops-that-runs-itself, burned by/fearing spammy AI-SDRs. AQ's dogfood IS the reference customer. Short cycle, Open/Try→Full. THE beachhead.
2. Security/governance-conscious mid-market ops (gated authority + audit = procurement story; higher ACV, Managed).
3. Agencies running many accounts (least-privilege isolation + honest client reporting resold as trust).
NOT first: RSI researchers (use free exo), big enterprise (no logos yet).

## MESSAGING
Core: "An autonomous operator you point at a real business goal — it does the work, measures the real outcome, and can't lie to you or act beyond the authority you granted. Not a research toy that rewrites itself; a practical operator that markets and runs a business, honestly and safely, and proves it on its own results."
Taglines: (1) "An autonomous operator that can't fake its own scoreboard." [strongest] (2) "Give it a goal. It runs the loop, measures the truth, and stops before it lies." (3) "Autonomy you can trust with real actions — because it can only do what you let it." (4) "It markets itself. Watch the real numbers." [once numbers exist] (5) "Not recursive self-improvement for the demo. A self-improving operator that runs your business — under enforced limits."
WIN-OR-LOSE DEMO: the agent REFUSING to report success it didn't earn (answers reward-hacking directly).
Objections answered: trust-with-money→can't act w/o your gate + request-never-self-grant; how-know-not-lying→grades vs ground truth + the refusal demo; isn't-this-exo→exo self-improves for capability, AQ is pointed at YOUR outcome + bounded authority; AI-SDR-was-spam→leads with honest+gated, won't blast unauthorized outreach, tells truth; small-team→Open/Try free + Full self-host you-own-it + audit trail.

## PRICING / GTM
- Open/Try FREE (non-negotiable — exo is free OSS; paid-only dies).
- Full ~$200-1,000/mo (self-host, "you own it"; don't underprice — honesty/safety is premium).
- Managed ~$2,000-8,000/mo, maybe outcome-linked (careful: Goodhart trap on ourselves).
- GTM: (1) DOGFOOD-AS-MARKETING (autonomy.talkingback.ai, public honest metrics INCLUDING misses) = flagship, uncopyable; (2) build-in-public on anti-Goodhart + authority engineering; (3) targeted beachhead outreach (human/disclosed-honest-agent as live proof of not-spam); (4) community; (5) TalkingBack brand.

## RISKS / WHAT MUST BE TRUE TO WIN
Risks: "runs a business" scorched by AI-SDR bust; differentiators individually copyable; honesty fatal to overclaim (one break = brand dead); SAFETY-THROTTLE COLD-START (won't get authority granted without track record → does little → hard to trust before useful); small team vs $650M/$26B/a16z.
Win iff: reproducible PUBLIC un-gameable demo of BOTH (a) hit a real external metric AND (b) refused to fake one; dogfood yields ≥1 honest real positive result; STAY IN LANE (never RSI/AGI); a beachhead where safety-throttle is a FEATURE; brand-perfect honesty discipline (every claim gated, every miss shown).

## SOURCES
exoharness.ai; github.com/exoharness/exo (docs/RSI.md, spec.md); Krentsel/Goyal LinkedIn; digg.com/tech/zkyw1tzg (Casado); the-decoder.com, thenextweb.com (Recursive $650M); weco.ai/blog (AIDE²); valueaddvc/siliconangle (Devin $26B/$492M ARR); artisan.co, coldreach.ai, amplemarket.com, salesrobot.co (AI-SDR backlash); arxiv 2605.21384 (SpecBench), 2511.21654 (EvilGenie), learnagentic.substack.com, tianpan.co (Goodhart); onestaff.ai, twin.so (guardrails table-stakes).
