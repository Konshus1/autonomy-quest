# Interview 4 — Models

**This question depends entirely on the mode chosen in `00-engine.md`. Check that first.**

## If they chose SUBSCRIPTION mode — there is nothing to decide here

The loop drives their TUI agent, so **the model is whatever that agent runs.** Codex runs what Codex
runs; Copilot runs what Copilot runs. There are no tiers to assign and no prices to compare,
because they are not paying per token.

Do not offer them a model menu. Do not read `data/models.json`. Just **ask the agent what it's
actually running** and write that down:

```sh
codex --version && codex exec "What model are you running right now? Answer with the model id only."
```

Record what it says, verbatim, and move on to the next question. If you propose a model the human's
subscription doesn't serve, you have invented a choice that does not exist.

```yaml
models:
  mode: subscription
  engine: codex
  model: "gpt-5.6-sol"     # WHATEVER THE AGENT REPORTED. Not what you assumed.
  monthly_estimate_usd: 0  # included in their plan
```

Skip to `05-budget.md`. **Everything below is for API mode only.**

---

## If they chose API mode — then you pick models

**Default: one OpenRouter key, tiered by job.**

One key reaches every model. The alternative is the human opening five provider accounts before the
system can run, which is where most setups die. If they already have provider keys, those work too —
this is a default, not a lock-in.

## The real decision: which model for which job

Not every job needs the expensive model. The system runs *continuously*, so the model mix is the
single biggest driver of what it costs to keep alive. Three tiers:

| Tier | Used for | Wants |
|---|---|---|
| **`reasoning`** | Deciding what to do, planning work, judging whether an outcome was good | Judgment. Worth paying for — this tier is where value is created or destroyed. |
| **`working`** | Doing the work — drafting, transforming, writing, calling tools | Competence at a sane price. Most of the volume lives here. |
| **`cheap`** | Classifying, extracting, summarizing, routing | Speed and near-zero cost. Do not spend reasoning-tier money here. |

## Never propose a model you have not confirmed exists

Your training data is stale. Models get deprecated, renamed, and re-priced constantly, and a model id
that was real when you were trained may 404 today. **Proposing a model from memory is how the human
ends up staring at "that model is not available."**

Two sources, in this order:

**1. Ask the provider what it actually serves.** This is ground truth and it takes one call:

```sh
curl -s https://openrouter.ai/api/v1/models | jq -r '.data[] | "\(.id)  in:\(.pricing.prompt)  out:\(.pricing.completion)"'
```

**2. Look up what's good.** You have web search — use it. Find what's currently strong for reasoning,
for bulk work, for cheap classification, and what it costs *today*.

`data/models.json` is a **cache**, not an authority. Refresh it from the live provider list and
record when you did. If it's stale or empty, that is expected — repopulate it, don't trust it.

Then propose a tier assignment **from models you just confirmed are live**, show the human the
estimated monthly cost at the cadence they'll pick in `05-budget.md`, and let them trade up or down.
A number they can see beats a lecture about model quality.

## How to run it

> "One OpenRouter key gets you every model — put it in `.env` yourself, don't paste it to me.
> Then I'll pick a model for each of three jobs from the dataset in the repo: the one that *thinks*,
> the one that *works*, and a cheap one for the busywork. Here's what that costs per month at the
> cadence you want…"

If they have strong feelings, honor them. If not, take the dataset's defaults and move on.

## Record

```yaml
models:
  provider: openrouter        # openrouter | anthropic | openai | ...
  tiers:
    reasoning: "..."          # ← chosen from data/models.json, not from memory
    working:   "..."
    cheap:     "..."
  monthly_estimate_usd: 0     # shown to the human, and they agreed to it
```

**Never** write a key into `instance.yaml`. Keys live in `.env`, which is gitignored, and the human
puts them there themselves.
