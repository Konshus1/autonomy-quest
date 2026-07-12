# Interview 7 — Web search

**Subscription mode: this is NOT a question. Turn it on, tell them you did, move on.**

**API mode: it costs money, so it IS a question — but only about cost, never about permission.**

Web search is not a nice-to-have. Without it the system is trapped inside the model's training data —
it cannot see a price that changed, a competitor that launched, a customer who churned, or anything
that happened after the cutoff. A learning loop with no window on the world doesn't learn; it drifts,
confidently.

So this is a **capability question, not a preference**, and it gets a hard rule at the end.

## Where search comes from

It depends on the execution mode chosen in `00-engine.md`, and the answer is usually free.

### Subscription mode — just enable it

All three agents ship native web search. **No MCP server required, and no extra cost** — it rides on
the plan they already pay for.

So **do not ask.** It costs them nothing, the mission needs it, and a question with only one sensible
answer is friction, not consent. Turn it on, verify it works, and tell them you did.

| Agent | Native search | How you enable it |
|---|---|---|
| **Claude Code** | `WebSearch` + `WebFetch` | already on |
| **Codex CLI** | native `web_search` tool | **off by default.** `--search` interactively, but the loop runs it non-interactively — so write `[tools] web_search = true` into `~/.codex/config.toml` and it's on for every invocation. |
| **Copilot CLI** | `web_search` + `web_fetch` | built into the binary |

> **The Codex trap — this is the one that will bite you.** Search is OFF by default, and the flag
> differs by mode: `--search` for the interactive CLI, `-c tools.web_search=true` for `codex exec`.
> The loop drives it with `exec`. Get this wrong and you ship an instance that **hallucinates instead
> of searching, and looks perfectly healthy doing it.** Put it in `config.toml` so it cannot depend on
> anyone remembering a flag. A capability that relies on someone passing the right argument is not a
> capability; it's a hope.

Then say, in passing:

> "Your plan includes web search, so I've switched it on — that's how the system will see prices,
> launches, and anything else that happened after the model's training cutoff. It costs you nothing
> extra. Here, watch: ⟨run one real search and show them the result⟩"

### API mode — the loop calls the model API directly

Search is a metered server-side tool. It works everywhere and it costs real money.

**Whatever they want to use, let them use it.** Model-provider search, a dedicated search API (Brave,
Exa, Tavily, Firecrawl, Serper), or something you've never heard of — if they have a key and they
want it wired in, wire it in. This is an open-source project on their machine, spending their money.
Your job is to make sure they know what it costs, not to gatekeep the choice.

**Look the prices up — do not read them out of this file.**

You have web search. Use it. Pricing changes, free tiers appear and vanish, and any table written
here is stale the week after it's committed. Search for the current cost of whatever they picked and
show them *today's* number.

As of this writing (verify, don't trust):

| Provider | Tool | Roughly |
|---|---|---|
| OpenRouter | `:online` / web plugin | ~$4 per 1,000 results; no extra credential needed |
| Gemini 3 | Grounding with Google Search | free tier, then ~$14 / 1,000 queries |
| Anthropic | `web_search` server tool | ~$10 / 1,000 searches + tokens |
| OpenAI | Responses `web_search` | ~$10 / 1,000 calls + tokens |

**Then say the honest thing, once:**

> "Search and model calls cost money on API mode, and how much depends entirely on how hard the
> system works. At today's prices that's roughly ⟨your estimate⟩ a month at the cadence you want.
> Those charges are yours — this runs on your machine, on your keys. I'll set a hard cap you can't
> blow through, and you can raise it whenever you like."

Give them the estimate, set the cap, move on. Don't lecture, and don't refuse to proceed because you
think it's expensive. That's their call to make.

### Driving a real browser

Some people will want to search through a logged-in browser profile (Google, Brave) with Playwright
rather than pay for an API. It works, but be straight about the trade: it's slow, it breaks on
bot-detection, and it points a persistent autonomous process at their live session cookies. Don't
steer a first-timer there. If they ask for it knowingly, that's their call too.

## THE HARD RULE — no search means no pretending

This is the one thing here that is not the human's call, because it isn't about *cost* — it's about
the system telling the truth.

> **If the mission requires research and NO search is configured at all, the loop must refuse to run.**

Not warn. Not degrade quietly. Halt, and say why.

This is not gatekeeping their choice of provider — pick any provider, pay any price, that's theirs.
It's about the one configuration that is simply broken: a loop with **no** window on the world, asked
to report on the world. It will produce fluent, plausible, entirely invented answers — forever, while
passing every health check — and file them as *learnings*, which then poison every future cycle.
It is indistinguishable from working, which makes it worse than a crash.

Better a loop that halts and says *"you asked me to research, and I cannot see the web"* than one
that quietly makes things up.

Record the capability so the runner can enforce it at startup.

## Prove it, always

Whichever mode they're in: **run one real search and show them the result** before you record it as
working. Ask for something no model could know from training data — today's price of something, this
week's release.

A capability you asserted is not a capability you have. That is the same rule `verify.sh` lives by,
and it applies to the thing that *checks* the world just as much as to the loop that turns.

## Record

```yaml
web_search:
  mode: subscription          # subscription | api | none
  provider: "codex"           # codex | claude-code | copilot | openrouter | gemini | anthropic | openai
  enabled: true
  # Codex ONLY: search is off unless launched with --search. If mode is subscription and the
  # engine is codex, this MUST be true or the loop is blind and will not know it.
  codex_search_flag: true
  verified_at: "2026-07-12T13:00:00Z"   # you RAN a search and saw a real result
  cost_note: "included in subscription"  # or the $/1k figure the human agreed to
  required_by_mission: true              # if true and enabled is false -> the runner REFUSES to start
```
