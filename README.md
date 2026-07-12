# autonomy-quest

**Stand up a system that works toward your mission, learns from what happens, and gets better at it — on your own box, in one container.**

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

By default, **one Linux container** holding the whole system:

- **Postgres + Apache AGE** — relational *and* graph in a single database. AGE gives you openCypher
  over Postgres, so the system can hold relationships without you running a second datastore.
- **The loop runner** — the engine that does work, records what happened, and learns.
- **The model gateway** — one **OpenRouter** key reaches every model. Other provider keys work too.
- **A local web UI** — to watch the loop, steer it, and change the mission.

One `docker run`. Nothing phones home. See [`container/README.md`](container/README.md).

---

## What v1 does and does not do

| | |
|---|---|
| ✅ **Local box** — your machine or a Linux box you own | ❌ **Cloud providers** — not in v1; credential handling is still being designed |
| ✅ **One container**, self-contained | ❌ **Hosted-for-you** — no managed service today |
| ✅ **Your keys, your data, on your disk** | ❌ **Windows native** — WSL2 path is being worked out |

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
interview/                ← the six decisions that aim your instance
templates/                ← starting missions. running-a-business is the flagship.
container/                ← the single-container build
data/models.json          ← maintained model capability/cost data (helps the agent pick models)
install.sh                ← reads instance.yaml, brings the system up
scripts/verify.sh         ← proves the loop actually turned. Not optional.
docs/what-this-is.md      ← the frame, in full
```

---

## Definition of done

**Installed is not done. A turning loop is done.**

`scripts/verify.sh` will not pass until the datastore answers, the model gateway completes a real
call, and the loop has run **at least one full cycle** — did work, recorded what happened, learned
something — with a row you can point at to prove it. A bootstrap that cannot prove the loop turned
is not finished, and it will tell you so.
