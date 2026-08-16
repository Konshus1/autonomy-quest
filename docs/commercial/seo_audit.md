# SEO audit — agentricity.com (research for loop B / #6096)

Live audit of `https://agentricity.com/`, 2026-08-16. Research artifact only — findings, not changes.
Feeds the B-acquisition SEO rubric (§ the harness in `optimization_system_plan.md`). Every fix below is
a no-risk static addition, but shipping it to the live site is Kevin-gated.

## What's already good
- **`<title>`** present + descriptive + keyword-bearing ("Agentricity — an autonomous operator that runs
  your business and does your marketing"). ~78 chars — slightly long (Google truncates ~60); candidate
  to tighten so the key phrase isn't cut in SERPs.
- **meta description** present.
- **viewport** set (responsive/mobile-friendly — a ranking factor).
- **exactly one `<h1>`** (correct document outline).
- **Performance is excellent**: ~40KB homepage, ~0.4s total load, ~0.23s TTFB. Core Web Vitals are a
  ranking factor and this is a real, already-won advantage (static, no framework bloat).
- Legal pages carry canonicals.

## Gaps (concrete, fixable)
| # | Gap | Impact | Fix |
|---|-----|--------|-----|
| 1 | **No `robots.txt`** (404) | crawlers have no directives; no sitemap pointer | add `robots.txt` allowing crawl + `Sitemap:` line |
| 2 | **No `sitemap.xml`** (404) | search engines must discover pages by luck | generate `sitemap.xml` listing every public URL |
| 3 | **Homepage has no `canonical`** | duplicate-URL ambiguity (apex vs www, trailing slash, query params) | add `<link rel="canonical" href="https://agentricity.com/">` |
| 4 | **No Open Graph tags** (0) | shared links (Slack/LinkedIn/iMessage/Discord) show no title/description/image card — kills social CTR | add `og:title/description/image/url/type` + a share image |
| 5 | **No Twitter Card** (0) | same, on X | add `twitter:card=summary_large_image` + fields |
| 6 | **No JSON-LD structured data** (0) | misses rich results + entity understanding | add `Organization`, `Product`+`Offer` (the two SKUs), and `FAQPage` (the site already has an FAQ section) JSON-LD |
| 7 | **No favicon** (404) | weaker brand in tab/bookmarks/SERP; some crawlers expect it | add favicon set |

## Notes for the rubric / strategy
- **Technical SEO is a one-time foundation** (items 1–7) with lasting payoff; do it once, do it right.
- **Content/keyword strategy is the ongoing lever** — needs its own research: who searches for this
  (developers evaluating self-hostable AI automation / autonomous agents you can own), what terms, and
  whether the copy targets them. The homepage currently leads with "runs your business and does your
  marketing" — broad; keyword research should confirm that's what the buyer actually searches.
- **Measure, don't assume:** pair any SEO change with the B-analytics slice (organic sessions,
  impressions/clicks via Search Console, funnel visit→signup→purchase) so we can tell if it worked —
  the honesty/verification invariant applies to marketing claims too.
- **Structured-data honesty:** `Product`/`Offer` JSON-LD must reflect the *real* $299 / $99-mo prices and
  real availability; `AggregateRating`/review markup stays ABSENT until there are real reviews (same
  proof-honesty rule as the G loop — no fabricated ratings for a rich-result star).
