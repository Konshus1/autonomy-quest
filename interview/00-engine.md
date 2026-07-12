# Interview 0 — The execution engine

*Runs first, before the mission, because it determines what you're able to do for the rest of
the interview.*

**You are the base agent.** The human already had you — you're Claude Code, Codex CLI, GitHub
Copilot CLI, or a desktop agent app. They did not install anything to get here. That's the point:
**bring your own agent.**

But the *resident* engine — the thing that turns the loop forever after you leave — may not be you.
You are here for an hour. It lives here.

## What to establish

**1. Which agent accounts does the human actually have?**

Ask plainly. Don't assume they have the one you happen to be.

> "Which of these do you already pay for or have access to — Claude, OpenAI/Codex, GitHub Copilot,
> or an API key on its own?"

This matters because it decides what the resident engine can be, and because **you can install
another TUI for them if they approve it.** All three install cleanly from npm/Homebrew/WinGet on a
normal box. If the human has a Copilot subscription and you're Claude Code, the right answer may
well be to install Copilot CLI as the resident engine and step aside. Offer it; don't be
territorial about it.

**Never install an agent without asking.** You are putting a persistent, autonomous process on
someone's machine. That gets consent, out loud, every time.

**2. Subscription or API? This decides what the system costs to keep alive.**

The loop needs a model on every cycle, forever. There are two ways to give it one, and they have
completely different economics.

| Mode | How the loop executes | Cost | Constraint |
|---|---|---|---|
| **Subscription** *(default)* | the loop **drives a TUI agent** in a terminal — Codex, Claude Code, or Copilot — under the plan they already pay for | **flat.** Marginal cost per cycle ≈ zero. Web search included. | **rate limits**, not dollars. The agent will tell them when they're near one. |
| **API** | the loop calls the model API directly with a key | **metered.** Tokens, plus $10–14 per 1,000 web searches. | none, but it adds up. |

**Default to subscription mode when they have a subscription** — it's what they already own, the
search comes bundled, and the cost of running continuously collapses toward nothing.

**If they have an API key and no subscription**, that's fine and they probably already know how API
billing works. But warn them once, plainly, and then let them get on with it:

> "Heads up: on API mode the loop pays per cycle — tokens plus about $10–14 per thousand web searches.
> That's real money for a system that runs continuously. We'll set a hard cap you can't blow through,
> and I'll show you the monthly estimate before you commit to it."

Do not lecture beyond that. Set the cap in `05-budget.md` and move on.

**3. Where is this going to run?** (This is the OS question, and it has teeth — see `03-datastore.md`.)

| Box | Path | Note |
|---|---|---|
| **macOS** *(clean default)* | native — Homebrew Postgres, AGE compiled from source | the full story works |
| **Windows + WSL2** *(recommended for Windows)* | native inside WSL2 | Linux underneath, so AGE works. Also where a TUI agent behaves properly. |
| **Windows, native, no WSL2** | Postgres yes, **graph no** | Apache AGE has no Windows build. Say so out loud — see below. |
| **Linux** | native | works |
| **Any of the above + Docker** | the single container | self-contained. For people who'd rather not install Postgres on their actual machine. |

### WSL2: check for Windows PATH bleed BEFORE you install anything

WSL2 **inherits the Windows PATH by default.** So inside Ubuntu, `npm` and `codex` can silently
resolve to the *Windows* binaries under `/mnt/c/...` while `node` is the Linux one. You then get a
hybrid stack that looks installed and cannot possibly work — e.g. a Windows-installed Codex being
executed by Ubuntu's Node 12, which dies with `SyntaxError: Unexpected reserved word` because Node
12 has no top-level `await`. Nothing about that error tells you the real cause.

Check it, every time, before installing:

```sh
which node npm codex     # anything under /mnt/c/ is a WINDOWS binary leaking into Linux
npm config get prefix    # a C:\... prefix means npm -g installs to WINDOWS, not Linux
node --version           # Ubuntu 22.04 ships Node 12 — too old for the agent CLIs
```

If any of them point at `/mnt/c/`, fix it before going further:

```sh
# 1. get a real Linux Node (Ubuntu 22.04's default is Node 12 — too old)
sudo apt-get purge -y nodejs libnode-dev
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
hash -r
which node npm           # BOTH must now be /usr/bin/... — verify, don't assume

# 2. stop Windows PATH bleeding into Linux (optional but cleanest)
printf '[interop]\nappendWindowsPath = false\n' | sudo tee -a /etc/wsl.conf
# then from PowerShell: wsl --shutdown   (and reopen the shell)
```

Note the trade-off honestly: `appendWindowsPath = false` also stops you calling Windows
executables (`code.exe`, `docker.exe`) from inside WSL. If the human relies on that, skip it — a
correct NodeSource install with `/usr/bin` ahead of `/mnt/c` on PATH is enough.

### WSL2: install onto the LINUX filesystem, never /mnt/c

Clone and install under `~` (e.g. `~/autonomy-quest`) — **not** `/mnt/c/...`.

`/mnt/c` is the Windows drive mounted into Linux via DrvFs, and it behaves like Windows, not Linux:

- **POSIX permissions don't stick.** `chmod +x install.sh` may silently not take, so the scripts are
  not executable and you get a confusing "permission denied" on a file that visibly has an `x` bit.
- **It is slow** — often 10-20x slower for the many-small-files work that `pip`, `venv` and `git`
  do constantly.
- **Postgres must never live there.** A database on DrvFs will be slow and can corrupt: `fsync`
  semantics across the Windows/Linux boundary are not what Postgres assumes.

Working on the Windows drive from inside WSL feels convenient — the files show up in Explorer — and
it will cost you hours in failures that look like bugs in the kit and are not.

```sh
cd ~                       # the LINUX home, not /mnt/c
gh repo clone Konshus1/autonomy-quest
cd autonomy-quest
```

If the human wants the files visible from Windows, that is what `\\wsl$\Ubuntu-22.04\home\<user>`
is for — Explorer can read the Linux filesystem. Do not invert it.

**On Windows without WSL2, be honest rather than clever.** Apache AGE does not support Windows.
The system will still run, record, and learn — but it will not have the relationship graph, so it
reasons across its history less well. Tell them that plainly and offer WSL2 or Docker instead. Do
not quietly install a weaker instance and let them believe they got the whole thing.

## Record

```yaml
engine:
  bootstrap_agent: "claude-code"       # who ran the install (you)
  resident_agent: "codex"              # the TUI agent the loop drives from now on
  mode: "subscription"                 # subscription | api
  accounts: ["anthropic", "github"]    # what they actually have
  installed_by_us: false               # did we put a new TUI on their machine? they approved it?
  # Codex ONLY: the resident agent must be launched with --search or the loop is blind.
  # See interview/07-web-search.md.
  launch_flags: ["--search"]
platform:
  os: "macos"                          # macos | windows-wsl2 | windows-native | linux
  mode: "native"                       # native | container
```
