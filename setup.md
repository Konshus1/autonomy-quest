# setup.md — the spine

> **You are a coding agent reading this file.** A human has pointed you at this repo and asked you
> to stand up an autonomous operations system that will work toward *their* mission.
> Read this file top to bottom, then follow it. Do not skip the interview.

This file is the only entry point. Everything else in the repo is something this file sends you to.

---

## 0. Preconditions — check before you do anything

Run these and report the results to the human.

**On Windows: read [`docs/windows-wsl2.md`](docs/windows-wsl2.md) FIRST.** Every failure mode in it
was hit on a real box, and every one of them produces an error that lies about its own cause.

```sh
uname -a                 # macOS, Linux, or Windows (see below)
git --version            # REQUIRED
python3 --version        # REQUIRED
```

**Only `git` and `python3` are hard requirements.** Everything else depends on what the human
chooses in the interview, so do not gate on it here. In particular:

- **Docker is NOT required.** It is needed *only* if they pick the container path in
  `interview/00-engine.md`. The default is a native local install. Do not stop for a missing
  Docker; note it, and let the interview decide whether it matters.
- **Postgres is NOT required yet.** Installing it is your job, after the interview tells you which
  shape they want.

A missing *hard* requirement stops you. A missing *optional* one is a fact you carry into the
interview. Do not turn an optional dependency into a mandatory gate — that turns a working setup
into a dead end for no reason.

Then confirm with the human, out loud:

- **Where is this running?** v1 supports a **local box only** (their machine, or a Linux box they
  own). Cloud provider setup is not in v1 — if they want cloud, stop and say so.
- **Do they have a model API key?** Default is a single **OpenRouter** key, which reaches all
  models. Other provider keys work too. Do not ask them to paste it into chat — have them put it
  in `.env` themselves (see step 4).

---

## 1. Understand what you are building

Read `docs/what-this-is.md` before the interview. Summary, so you carry the right frame:

This is **not** a CI/CD or DevOps system. It is an **autonomous operations system that continuously
evolves toward the user's mission.** It runs a loop: it does work, it observes what happened, it
learns, and it changes its own behavior to get better at the mission. The mission is supplied by the
human. The reference instance runs a software product; the flagship template is **running a
business**. Someone else's could be a research program, a trading desk, a nonprofit, a farm.

Your job in this setup is to (a) learn their mission, (b) install the components that mission needs,
and (c) leave behind a system that is *running* and *learning*, not a pile of installed packages.

---

## 2. The interview — this is the important part

**Do not install anything until the interview is done.** The interview is what aims the instance.
An unaimed instance is a toy.

Work through `interview/` in order. Each file is a decision with a recommended default; you ask, you
listen, you record the answer in `instance.yaml`. Take the default whenever the human is unsure —
the defaults are chosen to work.

| # | Decision | File | Default |
|---|----------|------|---------|
| 0 | **Engine & box** — which agent accounts they have, and what they're installing onto | `interview/00-engine.md` | you, on their OS, native |
| 1 | **Mission** — what is this instance trying to achieve, and how will it know it's winning? | `interview/01-mission.md` | *no default — must be answered* |
| 2 | **Templates** — start from `running-a-business`, or blank? | `interview/02-template.md` | `running-a-business` |
| 3 | **Datastore** — Postgres + Apache AGE (graph + relational in one), or Postgres alone? | `interview/03-datastore.md` | Postgres + AGE |
| 4 | **Models** — which model for which job, at what cost? | `interview/04-models.md` | OpenRouter, tiers from `data/models.json` |
| 5 | **Autonomy budget** — how much may it spend, and how far may it act without asking? | `interview/05-budget.md` | small, ratchets up on proof |
| 6 | **Surfaces** — UI, chat channel, email, none? | `interview/06-surfaces.md` | local web UI |
| 7 | **Web search** — how does the system see the world? | `interview/07-web-search.md` | whatever the engine already has |

Write every answer to `instance.yaml`. That file is the instance's identity. If you finish the
interview and `instance.yaml` has an empty mission, you have failed — go back and ask again.

### On the mission question (#1)

This is the one people fumble. Push for something a machine can act on. "Grow the business" is not a
mission; it's a mood. Get to: *what outcome, measured how, by when, and what may it touch to get
there?* Give them the `running-a-business` template as a worked example if they're stuck.

---

## 3. Install — driven by the interview, not by this file

Read `instance.yaml`, then run the installer for the shape they chose:

```sh
./install.sh            # reads instance.yaml, installs what the answers called for
```

**The default is a native local install** on their own machine — Postgres (plus AGE where the OS
supports it), the loop runner, and the model gateway, installed directly. Most people should never
need Docker.

**The single container is the alternative substrate**, for people who'd rather not put Postgres on
their actual machine, or who are on Windows without WSL2. It brings up Postgres + AGE + the full
schema and a status UI in one Docker run, then idles until this setup process has aimed the
instance. It does not replace the interview and it does not auto-start an unaimed loop. See
`container/README.md`.

Everything is **idempotent**. Re-running `install.sh` on a live instance must not destroy it. If you
are about to do something destructive, stop and ask the human first.

---

## 4. Secrets — the human handles these, never you

Copy `.env.example` to `.env` and tell the human which keys to fill in. **Do not ask them to paste a
key into the chat, and never echo a key back.** Read them from the environment at runtime.

`AQ_APPROVAL_TOKEN` gates the UI's approve action. `install.sh` generates one when it is absent,
saves it in `.env`, and prints a local retrieval command rather than the token value. The container
entrypoint generates one on startup unless the token was supplied in the environment, and likewise
prints a retrieval command rather than logging the secret. Do not replace it with a shared example
token.

---

## 5. First loop — prove it's alive

Installation is not success. A *running loop* is success. Before you tell the human you're done:

```sh
./scripts/verify.sh
```

This must show, from ground truth and not from a log line that claims it:

- the datastore answers a query,
- the model gateway completes one real call,
- the loop has executed **at least one full cycle** — did work, recorded what happened, learned
  something — and you can point at the row that proves it.

If any of those three is false, the system is **not** set up. Say so plainly. Do not report success
on a system whose loop has never turned.

---

## 6. Make it actually run — without you

A loop that only turns while a human has a terminal open is not an autonomous system. Schedule it:

```sh
./scripts/schedule.sh install     # systemd (Linux/WSL2), launchd (macOS), Task Scheduler (Windows)
./scripts/schedule.sh status      # is it ACTUALLY turning? checks ground truth, not "did we install it"
```

**`status` does not ask the scheduler whether it is running.** The scheduler can happily report a
live process whose loop died hours ago — that is the failure this whole system exists to refuse.
It checks the only thing that means alive: did a full cycle *complete* — acted, recorded, and
**learned** — recently?

---

## 7. Hand over

Show the human:

- their mission, read back from `instance.yaml`,
- the first thing the system did, and what it learned from it,
- where the UI is, and how to change the mission later,
- what it may do on its own today, and what it must ask about.

Then stop. The system takes it from here.
