# Doctrine — the seven invariants

*The [frame](what-this-is.md) tells you what this system is. This tells you what keeps it honest.
Seven rules run underneath every part of the kit — the interview, `verify.sh`, the schema, the
sharing flow. They are here in one place because they are portable: they hold for any autonomous
loop you build, not just this one. Each is stated as a rule, then as the concrete place the kit
already enforces it — because a principle you only write down is not a principle, it is a wish.*

---

## 1. Promote a learning only after it changes behavior — never because it exists

The tempting shortcut is to treat "the learning was recorded / imported / installed" as "the system
is now better." It is not. Presence is a proxy, and the proxy is never the invariant. A learning has
value only if the agent, holding it, *does something different* — and the only way to know that is to
test the behavior with and without it and see the difference. An imported insight that changes no
decision is dead weight that makes the prompt longer and the system slower.

So the gate on adopting a learning is a **behavior-change test**, not a presence check. "Did this
arrive?" is the wrong question; "does the agent act differently because of it, and better?" is the
right one.

*In the kit:* cross-mission learnings arrive **staged and inert** (`aq.py share import`) and are
adopted only through `aq.py share review` / `share promote`, with a reason — never automatically on
arrival. Nothing an outside instance sends can change this instance's behavior until it has been
adjudicated here.

## 2. Anything the loop gets to define for itself, it will define as infinite

A loop optimizing a number will move that number by whatever means it has. If it also gets to choose
*what counts*, it will choose "everything." This is the single hole that produced the kit's most
important scar: an instance told to track "60 models" with an open-ended measure ran to **50,082**,
because nothing external said which 60, and "more" always scored higher.

The frontier of a measure — and the frontier of curiosity, when you add it — must be **seeded from
ground truth the loop cannot edit**: a catalog, an allowlist, an authoritative list. The loop's job
is to keep the *in-scope* set fresh; it must be structurally unable to grow scope to move the number.
Measurement-scope and exploration-frontier are the *same* self-certification hole, and one rule
closes both: *no actor defines the boundary of its own success.*

*In the kit:* every mission must set `measure.target` + `measure.goal`, prefer `count(DISTINCT …)`,
and scope-seed a bounded set (`interview/01-mission.md`, "Measures need a CEILING"). `verify.sh`
**fails** a measure with no ceiling — it does not merely warn. At target the loop shifts to
maintenance and honestly does nothing rather than manufacturing work.

Optional curiosity uses the same rule. If `curiosity.enabled` is set, `verify.sh` requires a finite
standing exploration budget and a frontier seeded from an external authority. The runner validates
that frontier before spending, applies the same satisfaction/overshoot tripwire to the exploration
metric, cuts curiosity before mission work under cost pressure, and stages exploration output
inertly. A curiosity proposal cannot change behavior unless it later survives review and promotion
with local behavior-change evidence; if no curiosity proposal earns promotion over the ratchet
window, curiosity loses budget.

## 3. Belief is not reality — reconcile them on purpose

A loop carries a model of the world (what work exists, what is running, what is done). That model
drifts from what is actually true, silently, and a system that trusts its own stale model looks alive
while accomplishing nothing. The defense is a deliberate cycle: **declare the expected state → probe
reality → reconcile the drift → report, or take a gated action.** Never let a cached belief stand in
for a live fact — recompute the fact at decision time.

*In the kit:* `reconcile_orphaned_runs()` is one instance of exactly this — at the top of each cycle
it probes for runs the loop *believes* are in flight but which reality shows were abandoned
(a hard kill mid-cycle), and marks them terminal so the world model matches the ground. `verify.sh`
gates on a **completed cycle joined to a learning**, not on "is the process up" — the liveness proof
is the row, not the running flag.

## 4. Put correctness in the structure, not in remembering — and re-verify even when authorized

Two halves of one idea. First: a rule that lives only in a human's memory (or a doc, or a comment)
will be forgotten under pressure. Move it into **structure** — a schema constraint, a required field,
a gate that fails — so the wrong state is unrepresentable rather than merely discouraged. The system
should be correct because it *cannot* be otherwise, not because someone remembered.

Second: **authorization is not a substitute for a ground-truth check.** Being allowed to do a thing
does not mean the thing is still the right thing — the world may have moved since approval. An
authorized action re-reads the live state and acts on *that*, not on the stale report that earned the
approval. This is what lets the loop ask humans less *safely*: it acts on verified reality, not on a
proxy someone once signed off.

*In the kit:* `verify.sh` refuses to report ALIVE on a system that only *installed* (a completed
cycle must exist); the mission gate *fails* rather than warns on a ceiling-less measure; the DB
schema makes an unadjudicated promotion unrepresentable. The rule is enforced by the shape of the
system, so it holds whether or not anyone remembers it.

## 5. Approval is authorization to execute, not a decoration

Parking work is only honest if approval actually releases that exact work to run. A UI button that
changes a label but never reaches the executor is worse than no button: it trains the human to think
they answered while the loop silently moves on.

Approval also has to be a real gate. A local UI is not an excuse for a write endpoint that accepts
any request that can reach the port. If approval is the human boundary, the write that crosses it
must fail closed when the approval secret is absent and must reject missing or wrong tokens.

*In the kit:* approved parked work is represented structurally as `work.status='pending'` with
`approved_at` set. `Loop.cycle()` selects approved work by `approved_at` before asking the model to
decide new work, validates it through `runner.approval.assert_valid_approval()`, then sends it
through the same `execute_work()` act→record→learn path as autonomous work. `/api/approve/{id}` is
token-gated by `AQ_APPROVAL_TOKEN`, fails closed when the token is unset, performs only the guarded
`awaiting_human -> pending` transition, and calls the same approval invariant on the returned row.

## 6. A safety claim needs a red test first

It is easy to write a green test for the code you wish you had. That does not prove you fixed the
bug; it proves the new code satisfies itself. For high-adversarial findings, preserve the failing
shape first: the row, request, or loop state that currently violates the invariant. Then make that
same shape pass.

*In the kit:* approval execution and approval auth have focused tests around the old failure shape:
approved pending work must execute before a new decision is made; pending work without `approved_at`
is not human approval; `/api/approve` returns 403 when the token is unset or wrong and only approves
with the configured token. The task evidence is kept under `artifacts/task_3303/` so reviewers can
see what failed before the fix.

## 7. Output liveness is a record, not a running process

The loop is alive only when work passed all the way through output and learning. A process can be up
and doing nothing useful; a UI can render; a log can say "started." None of those are output
liveness. The proof is durable state that shows the loop acted, recorded the result, and learned
from it.

*In the kit:* `verify.sh` gates setup on a completed `runs` row joined to a `learnings` row, and the
runner writes act→record→learn through one completion path. Approved work uses that same path, so a
human approval cannot create a special side channel that looks accepted but never produces a run.

---

*These seven are the reason more than one instance can safely learn from another (invariant 1), why a
mission can be left running unattended without running away (invariant 2), why "it's alive" is a fact
and not a hope (invariants 3 and 7), why the approval boundary is real (invariant 5), why fixes prove
the bug they claim to fix (invariant 6), and why the whole thing stays correct as it grows
(invariant 4). Build your own loop on top of them.*
