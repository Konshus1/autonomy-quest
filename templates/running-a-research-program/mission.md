# Template — Running a research program

*Maintain a body of verified knowledge over a bounded, changing set of things. The flagship example
is the reference instance: "keep verified pricing/specs current for the models on the OpenRouter
catalog." This template exists because a research mission is where the CEILING-AND-SCOPE trap
(interview/01-mission.md) bites hardest — a bare "track N things" ran a real instance to 50,082.*

## The worked example (copy this shape, change the domain)

```yaml
mission:
  objective: "Maintain verified pricing, context-window sizes and perceived quality for every model
              on the OpenRouter catalog, kept fresh within 30 days."
  measure:
    what: "in-scope items with research fresh within 30 days"
    # COUNT A DISTINCT, IN-SCOPE SET — never count(*):
    where: "select count(distinct item_key) from research
            where in_scope and researched_at > now() - interval '30 days'"
    # THE TARGET IS A LIVE QUERY — the whole in-scope set — so 'done' means 'all of it is fresh'
    # and self-updates as the catalog changes:
    target_query: "select count(distinct item_key) from research where in_scope"
    goal: reach_and_maintain
  horizon: "reviewed weekly"
  boundaries:
    may_act_alone:
      - "research items on the public web and record verified facts with source URLs + timestamps"
      - "re-verify stale in-scope records"
    must_ask_first:
      - "publish or export anything"
      - "spend money"
      - "contact anyone"
```

## The two things that make it safe (do NOT skip either)

1. **Seed `in_scope` from an EXTERNAL ground-truth list, before the first cycle.** Fetch the
   authoritative set (a catalog, an API's model list, a curated allowlist) and mark exactly those
   rows `in_scope = true`, with a `scope_source`. **The loop must never set `in_scope` for itself** —
   a loop that defines its own scope defines it as infinite. Its job is to keep the *in-scope* set
   fresh; it physically cannot grow scope to move the number.

2. **Retain sources + timestamps** on every record (`source_url`, `researched_at`) so freshness is
   auditable and "verified" means something a human can re-check.

## What good steady-state looks like

Cycle 1..N: research toward the in-scope total. Then the number PLATEAUS — the loop re-verifies the
stalest records to hold freshness, and on a cycle where nothing is stale it honestly does nothing
("not inventing busywork") rather than manufacturing rows. **A maintain research program is quiet at
rest.** If it is always busy, the measure has no ceiling or the scope is loop-defined — go back to
interview/01-mission.md.
