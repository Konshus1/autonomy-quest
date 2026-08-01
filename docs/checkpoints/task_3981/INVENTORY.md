# Inventory — Ralph causal / self-improvement vs AQ (checkpoint baseline)

## In TalkingBack (origin/main @ TB worktree)

| Surface | Portable today? | Notes |
|---|---|---|
| `ralph_portable/surprise_resolution.py` | **YES** | Stdlib-only; SHA-pinned; proven in AQ + docker COPY (v1) |
| `app/services/ralph_causal_principle_*.py` | **NO (yet)** | Pure functions exist, but wired to TB principle library / DB adapters |
| `causal_principle` DB (15 principles, ~106k usage) | **TB-local** | Not in AQ schema |
| Learning-loop principle injection (BB #521) | **TB-local** | Needs AQ-shaped brief/adapter |
| Formal causal planning pipeline | **Out of AQ v1 scope** | BB #1229 |

## In AQ public main (33f41b8)

| Surface | Status |
|---|---|
| Mission loop + learnings + approve UI | Shipped |
| Bounded curiosity | Claimable with precise wording |
| Container substrate (Postgres+AGE+UI) | Shipped; copy-tested as v0 |
| Parent→child clone/copy-with-mod | **Not shipped** (decision #721); #3981/#3856 lane |
| Formal causal principles store | Absent |

## Next checkpoint candidates (ordered)

1. **v2** — agent-followable doc: how to build/use the portable surprise core from AQ interview/setup (decision #721 posture)
2. **v3** — minimal **portable principle selector** (stdlib; fixture library; no TB DB) + container test
3. **v4** — operator-mediated container copy harness (reuse #3981 image lineage; spawn request only)
