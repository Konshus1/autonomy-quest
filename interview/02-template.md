# Interview 2 — Template

**Default: `running-a-business`.**

A template is a starting shape: the kinds of work the loop knows how to do, the signals it watches,
and a first cut at what "better" means. It is a starting point, not a cage — the instance evolves
away from it immediately.

## Options

| Template | Use when | What it gives you |
|---|---|---|
| **`running-a-business`** *(default, flagship)* | The mission is commercial — customers, revenue, delivery, pipeline | Work types for outreach, delivery, follow-up, ops. Watches pipeline, cash, churn. "Better" = the mission's measure moves. |
| `running-a-product` | The mission is a software product | Ship / observe / learn. Watches usage, errors, deploy health. |
| `running-a-research-program` | The mission is knowledge | Hypothesis, experiment, result, revised prior. Watches which questions are still open. |
| `blank` | None of the above fits, and they know their domain well | Nothing but the loop. They define the work types. Costs a real session of setup. |

## How to run it

Read them the mission they just gave you, then ask which of these it sounds like. Usually obvious.

Take `running-a-business` when unsure and when the mission has customers or money in it. Only take
`blank` if they have a clear domain that genuinely doesn't fit — `blank` means they hand-author what
the other templates gave them for free, and most people who pick it regret it.

## Record

```yaml
template: running-a-business
```
