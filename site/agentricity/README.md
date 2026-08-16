# Agentricity marketing site — v1

Static, self-contained one-pager. No build step, no external requests (no CDNs,
no webfonts, no analytics). Deploy by serving this directory as-is; `index.html`
is the whole page, CSS is inlined for LCP, `js/main.js` is a small deferred
enhancement layer (the page works without it, minus form handling).

Spec: `docs/commercial/site_structure_and_offer.md` (§0–§10 structure) +
`docs/commercial/marketing_site_principles.md` (P1–P44 rubric).

## What is PLACEHOLDER (must be resolved before launch)

1. **Pricing (§6)** — the Full and Managed cards show `$XX/mo` / `$XXX/mo`
   tokens on purpose. See the `PRICING — PENDING` HTML comment above the
   section. Recommended ladder is in the spec (Full anchor ~$99/mo, Managed
   ~$249/mo). The cards honestly say "pricing being finalized during early
   access" so the tokens are not a lie if seen — but fill them before launch.

2. **Live dogfood metrics (§1 hero scoreboard + §4 proof tiles)** — every
   `[data-live-metric]` element renders an em-dash until the running instance
   wires real numbers in. The page says so explicitly ("Why the dashes?")
   rather than showing fake numbers. Wiring is a product task, not a site
   edit: populate the `[data-live-metric]` targets (plus a timestamp) from the
   instance's ground-truth store.

3. **Email-capture endpoint** — `SIGNUP_ENDPOINT` in `js/main.js` is an empty
   string. The ingest backend is wired separately and its URL/credential stays
   off the page and out of the repo; set the constant at deploy time. Until
   it's set, submission falls back to a prefilled `mailto:` to
   hello@agentricity.com with an honest "hit send and you're on the list"
   confirmation (i.e., the form is never a dead end and never claims a signup
   it didn't record).

4. **Contact mailbox** — `hello@agentricity.com` is used in 4 places
   (hero, pricing escape hatch, final band, footer). Create/forward the
   mailbox before deploy, or search-replace the address.

5. **Footer links** — `Docs` and `GitHub (open version)` are `href="#"` stubs
   marked `data-todo-link` + a `TODO(kevin)` comment. Fill real destinations
   or delete any link that won't exist at launch. The `@talkingback_ai` link
   is real.

## Deliberate design decisions (for the rubric review)

- **One conversion goal**: every CTA on the page (header, hero, all three
  pricing cards, final band) targets the same early-access capture. The only
  off-page links are the quiet mailto and the footer.
- **Primary CTA dominance (P7/P8)**: the hero form's "Start free" is the
  dominant CTA; the header CTA is a compact variant of the same action;
  everything else is ghost/text-weight.
- **Honesty gates (P39–P43)**: no testimonials, no logos, no user counts, no
  urgency devices anywhere; metric slots show "—" with an explanation instead
  of invented numbers; the refusal quote in §3 and the "verified today vs.
  hardening" split in the FAQ come straight from
  `docs/commercial/aq_capabilities_writeup.md`.
- **Fonts**: system stacks only (sans + mono) — zero font-loading cost, zero
  CLS, no licensing.
- **Verified at 1440px and 390px**; no horizontal scroll, tap targets ≥44px.
