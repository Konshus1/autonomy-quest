# autonomy-quest

**Stand up a system that works toward your mission, learns from what happens, and gets better at it — on your own box.**

This is not a CI/CD system and not a chatbot. It is an **autonomous operations system**: it does work
toward a goal *you* give it, watches the outcome, learns, and changes its own behavior to do better
next time. The loop is the point. Your mission is what the loop aims at.

The reference instance runs a software product. The flagship template is **running a business**.
Yours could be a research program, a nonprofit, a trading desk, a farm.

---

## Start here

You need a coding agent (Claude Code, Codex, or Cursor) and about an hour. Paste one of these
two prompts into it. That's the whole entry point.

**If you don't have the repo yet:**

```
Clone https://github.com/<org>/autonomy-quest, then read setup.md and follow it.
Interview me where it tells you to. Don't skip the interview.
```

**If you already have the repo:**

```
Read setup.md in this repo and follow it. Interview me where it tells you to.
Don't skip the interview.
```

The agent reads [`setup.md`](setup.md) — the spine — which walks it through checking your box,
**interviewing you** to learn your mission and shape the instance, installing only the components
your answers call for, and then proving the loop actually turned before it tells you it's done.

You will do most of your work in that interview. It is the part that aims the system.

---

## What gets installed

**By default, natively on your own machine** — most people never need Docker:

- **Postgres + Apache AGE** — relational *and* graph in one database. AGE gives you openCypher over
  Postgres, so the system holds relationships without a second datastore. *(No Windows build — see
  [`docs/windows-wsl2.md`](docs/windows-wsl2.md).)*
- **The loop runner** — does work, records what happened, learns, and changes what it does next.
- **The executor** — drives the coding agent you already pay for (Codex / Claude Code / Copilot) at
  **zero marginal cost, web search included**. Or a metered model API, if you'd rather.
- **A local web UI** — watch the loop, approve what it parked for you, change the mission.
- **A blackboard + MCP server** — so any agent can ask what this instance has learned.
- **A scheduler** — systemd / launchd / Task Scheduler, so it keeps running without you.

**The single container is the alternative substrate** — for people who'd rather not put Postgres on
their actual machine, or who are on Windows without WSL2. One Docker run brings up Postgres + AGE +
the full schema plus a status UI as an idle, initialized base. A coding agent still has to complete
the interview before the autonomous loop can be aimed. See [`container/README.md`](container/README.md).

Nothing phones home.

---

## What v1 does and does not do

| | |
|---|---|
| ✅ **Local box** — your machine or a Linux box you own | ❌ **Cloud providers** — not in v1; credential handling is still being designed |
| ✅ **Native install** (or one container, if you prefer) | ❌ **Hosted-for-you** — no managed service today |
| ✅ **Your keys, your data, on your disk** | ⚠️ **Windows** — WSL2 works; native has no graph layer. [Read this first.](docs/windows-wsl2.md) |

---

## Why more than one of these should exist

A single instance learns from a single history. That's a narrow education.

Because every instance is aimed at a different mission and shaped by a different interview,
instances **diverge** — they try different structures, different model mixes, different degrees of
autonomy. Most of what any one of them learns is local and worthless to you. But some of it isn't:
*this way of decomposing work beat that one; this guard caught a class of failure before it shipped;
this budget shape produced more value per dollar.* Those generalize.

So the instances get better, and — if their operators choose to share what generalized — the *field*
gets better faster than any single instance could on its own. Running variants and keeping what
works is how the whole thing compounds. That is the bet.

Sharing is **opt-in and off by default.** Your mission, your data, and your keys never leave your box.

---

## Repo map

```
setup.md                  ← the spine. The agent reads this. Start here.
docs/windows-wsl2.md      ← READ FIRST on Windows. The errors all lie about their cause.
interview/                ← the eight decisions that aim your instance (mission has no default)
templates/                ← starting missions. running-a-business is the flagship.
container/                ← the single-container Postgres + AGE substrate
data/models.json          ← maintained model capability/cost data (helps the agent pick models)
install.sh                ← reads instance.yaml, brings the system up
scripts/verify.sh         ← proves the loop actually turned. Not optional.
docs/what-this-is.md      ← the frame, in full
docs/doctrine.md          ← the invariants that keep any autonomous loop honest
```

---

## Definition of done

**Installed is not done. A turning loop is done.**

`scripts/verify.sh` will not pass until the datastore answers, the executor completes a **real**
model call, and the loop has run **at least one full cycle** — did work, recorded what happened, and
**learned something** — with a row you can point at.

That last one is the load-bearing part: the gate is a completed run **joined to a learning row**. A
system that acts and records but never learns is automation, not evolution, and it **cannot report
success here.** A bootstrap that can't prove the loop turned is not finished, and it will say so.
