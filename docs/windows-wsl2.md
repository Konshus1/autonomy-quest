# Windows / WSL2 — the failure modes

Every entry here was hit on a **real Windows 11 box**, not imagined. They share one nasty property:

> **The error message never names the real cause.**

You get `SyntaxError: Unexpected reserved word` and go looking for a syntax error. You get
`permission denied` on a file that visibly has the execute bit. You get a database that is
mysteriously slow and occasionally corrupt. Every one of them wastes an hour if you don't already
know what you're looking at — so read this before you install, not afterwards.

---

## Why WSL2 at all?

**Apache AGE has no Windows build.** AGE is what puts the relationship graph inside Postgres — the
thing that lets the system reason across its own history instead of merely accumulating it.

So on Windows you have three honest options:

| Path | What you get |
|---|---|
| **WSL2** *(recommended)* | Everything. It's Linux underneath: AGE compiles, bash works. |
| **Docker Desktop** | Everything, in one container. |
| **Native Windows** | Postgres yes — **no graph layer.** A genuinely weaker instance. |

Native is a legitimate choice. **Quietly installing it and calling it complete is not.** If the
human picks native, say plainly that the instance has no relationship layer.

---

## 1. WSL2 inherits the Windows PATH → a hybrid stack that cannot work

**Symptom**

```
$ codex --version
file:///mnt/c/Users/<you>/AppData/Roaming/npm/node_modules/@openai/codex/bin/codex.js:233
const childResult = await new Promise((resolve) => {
                    ^^^^^
SyntaxError: Unexpected reserved word
```

**What's actually happening.** WSL2 appends the Windows PATH by default. So inside Ubuntu:

```
node  -> /usr/bin/node                     (Linux — and Ubuntu 22.04 ships Node 12)
npm   -> /mnt/c/Program Files/nodejs/npm   (WINDOWS)
codex -> /mnt/c/Users/.../npm/codex        (WINDOWS)
npm config get prefix -> C:\Users\...      (installs -g into WINDOWS)
```

`npm install -g @openai/codex` therefore wrote Codex into the **Windows** npm prefix, and then
**Ubuntu's Node 12** tried to execute it. Node 12 has no top-level `await`. Hence the "syntax
error" — in a file with no syntax error.

It is a stack that *looks* installed and cannot possibly work.

**Check before you install anything:**

```sh
which node npm codex      # anything under /mnt/c/ is a WINDOWS binary leaking into Linux
npm config get prefix     # a C:\... prefix means npm -g installs to WINDOWS
node --version            # Ubuntu 22.04 ships Node 12 — far too old for the agent CLIs
```

**Fix:**

```sh
sudo apt-get purge -y nodejs libnode-dev
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
hash -r
which node npm            # BOTH must now be /usr/bin/... — verify, do not assume
```

If npm still resolves to `/mnt/c`, stop the bleed entirely:

```sh
printf '[interop]\nappendWindowsPath = false\n' | sudo tee -a /etc/wsl.conf
# then, from PowerShell:  wsl --shutdown     (and reopen the shell)
```

**State the trade-off honestly:** `appendWindowsPath = false` also stops you calling Windows
executables (`code.exe`, `docker.exe`) from inside WSL. If the human relies on that, skip it — a
correct NodeSource install is enough on its own.

---

## 2. Never install onto `/mnt/c`

**Symptom:** `permission denied` running `./install.sh` — on a file that visibly has an `x` bit.
Or a working install that is inexplicably glacial. Or, worst, a Postgres that corrupts.

**What's actually happening.** `/mnt/c` is the Windows drive mounted through DrvFs. It behaves like
Windows, not Linux:

- **POSIX permissions don't stick.** `chmod +x` can silently fail to take.
- **It's 10–20× slower** for exactly the many-small-files work that `pip`, `venv` and `git` do
  constantly.
- **Postgres must never live there.** `fsync` semantics across the Windows/Linux boundary are not
  what Postgres assumes. Slow *and* corruptible.

It feels convenient, because the files show up in Explorer. That convenience will cost you hours in
failures that look like bugs in this kit and are not.

**Fix — clone into the Linux home:**

```sh
cd ~                      # /home/<you>, NOT /mnt/c
gh repo clone <org>/autonomy-quest
cd autonomy-quest
```

If the human wants the files visible from Windows, Explorer can read the Linux side at
`\\wsl$\Ubuntu-22.04\home\<user>`. Go **that** direction, not this one.

---

## 3. The Codex Windows Store shim can be broken

**Symptom**

```
Program 'codex.exe' failed to run: An error occurred trying to start process
'C:\Program Files\WindowsApps\OpenAI.Codex_..._x64__.../codex.exe' ... Access is denied.
```

The Store-installed shim on PATH fails with **Access is denied**, while the bundled binary under
`AppData\Local\OpenAI\Codex\bin\...` works fine.

This matters beyond the box: the loop's executor does `which codex` and shells out to it. A broken
shim sitting first on PATH gives you an agent that cannot run *and* an error that explains nothing.

**On WSL2 this is moot — and that's the point.** Install a **Linux** Codex inside the distro
(`sudo npm install -g @openai/codex`) and keep the whole stack native to the distro. Do not try to
drive `codex.exe` from Linux.

---

## 4. Bubblewrap warning (benign)

```
Codex could not find bubblewrap on PATH ... Codex will use the bundled bubblewrap in the meantime.
```

Benign under WSL2 — it falls back to its bundled `bwrap`.

Worth knowing, though: **inside a Docker container, bwrap cannot create user namespaces at all**
(`No permissions to create a new namespace`), and *every* shell command the agent runs then fails.
The loop turns, the work fails, and it looks exactly like a database problem. If you see namespace
errors, that's the cause — see `runner/executor.py`, which detects an already-sandboxed environment
and stops double-sandboxing.

---

## 5. A capability proven on the Windows binary is not a capability you have

Codex web search is **off by default**, and the flag differs by mode:

- interactive: `--search`
- `codex exec`: `-c tools.web_search=true`

**The loop drives `codex exec`.** So put it in the config file, where it applies to both:

```sh
mkdir -p ~/.codex && printf '[tools]\nweb_search = true\n' >> ~/.codex/config.toml
```

And note the WSL2 trap specifically: if you proved search worked on the **Windows** Codex, that
proves nothing about the **Linux** one — and the `config.toml` you wrote may have landed in the
Windows profile. Re-prove it inside WSL, by asking for something no model could know from training
data and confirming it *visibly issues a search and cites a live URL*.

Get this wrong and you ship an agent that **hallucinates instead of searching, while looking
perfectly healthy.** It is the single worst failure mode in this system.

---

## 6. Apache AGE **does** build under WSL2 — CONFIRMED

The whole reason to choose WSL2 over native Windows is that AGE has no Windows build but *does*
compile on Linux. That was an assumption until someone tried it. Now it has been tried:

**AGE `release/PG14/1.5.0` compiles under WSL2 (Ubuntu 22.04) against PostgreSQL 14.** It did not
fail for WSL compatibility, and it did not fail for Postgres compatibility. The recommended Windows
path delivers what it promises.

The one trap: **`build-essential` does not include `flex` or `bison`**, and AGE needs both.

```
/usr/bin/flex -b -o'src/backend/parser/ag_scanner.c' src/backend/parser/ag_scanner.l
make: /usr/bin/flex: No such file or directory
make: *** [.../Makefile.global:774: src/backend/parser/ag_scanner.c] Error 127
```

`install.sh` now installs `build-essential flex bison postgresql-server-dev-<major>` before the
build, unconditionally.

---

## 7. Postgres version: Ubuntu 22.04 ships PG14, not PG16

`install.sh` defaults to `postgresql-16`. Ubuntu 22.04's default repos carry **PostgreSQL 14**, so
that package does not exist without adding the PGDG apt repository.

*(Status: predicted, being tested on a live box. This section gets the verbatim error and the fix
once it's confirmed — no claims here before they're true.)*

---

## 8. Surviving a reboot on Windows — THREE things, none of them the default

**A LINUX SCHEDULER CANNOT SURVIVE A WINDOWS REBOOT.** systemd inside WSL only helps once WSL is
*running*, and **Windows does not start a Linux VM nobody asked for.**

All three of these must be true, and on a fresh Windows box **none of them is**:

| # | What | Why |
|---|---|---|
| 1 | `systemd=true` in `/etc/wsl.conf` | WSL2 has **no systemd by default** — which is why `service postgresql start` reports a unit that does not exist |
| 2 | `loginctl enable-linger <you>` | a systemd **--user** service dies at logout without it |
| 3 | a **Windows** Task Scheduler entry that starts the distro at boot | nothing else will |

`schedule.sh install` does (1) and (2) and **prints** (3) for you to run in an admin PowerShell —
it does not silently reach into your Windows machine.

> **The trap we nearly fell into:** the first Windows box we tested **already had** `systemd=true`
> and `Linger=yes`. So a "successful" reboot there would have proved nothing about a fresh box. **A
> machine that already works tells you nothing about one that doesn't.** Configure all three every
> time, and verify.

**How to actually prove it** (this is the only honest test):

```powershell
# after a REAL reboot — do NOT open a WSL terminal first, that would START WSL and contaminate it
wsl --list --running     # LISTS without starting. Ubuntu must appear here.
```

Only if the distro is genuinely running is it safe to look inside — and then the proof is a
**heartbeat row timestamped after the reboot**. *"systemctl says the service is enabled"* is a
**proxy**: a service can be enabled inside a distro that never booted.

---

## 9. Scheduling under WSL2 — the systemd part works

**CONFIRMED:** `scripts/schedule.sh install` sets up a **systemd user service** under WSL2 and it
runs. `schedule.sh status` reports it active, and — the part that matters — ground truth agrees: a
full cycle completed. The loop is scheduled, not just installed.

**STILL OPEN: does it come back after a Windows reboot?** Nobody has tested it. WSL shuts down when
its last process exits, systemd in WSL2 needs `[boot] systemd=true` in `/etc/wsl.conf`, and a Windows
restart does not necessarily bring the distro back at all.

Until someone reboots the box and watches the loop resume **on its own**, "it survives a reboot" is a
claim, not a fact. This kit does not make claims it hasn't tested.

---

## 10. Subscription rate limits punish one-item-per-cycle work

Not a bug — a **strategy** finding, and it cost a real SLA.

On a subscription, **each cycle consumes a rate-limit slot.** Two instances ran nearly the same
mission (research ~60 LLM models):

- one decided *"research 20 models"* per cycle → reached **60/60 in four cycles**
- one decided *"research 1 model"* per cycle → hit the plan's rate limit and was still at **1/60**

Same kit, same mission. **The batching decision was the entire difference.** The decide prompt now
says so explicitly. If your work divides into similar items, do a batch in one cycle.

If you need burst throughput and can't wait out a 900s backoff, that is the honest argument for
**API mode** — you pay per token instead of per rate-limit slot.
