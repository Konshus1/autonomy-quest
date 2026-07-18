# Doctrine — the four invariants

*The [frame](what-this-is.md) tells you what this system is. This tells you what keeps it honest.
Four rules run underneath every part of the kit — the interview, `verify.sh`, the schema, the
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

---

*These four are the reason more than one instance can safely learn from another (invariant 1), why a
mission can be left running unattended without running away (invariant 2), why "it's alive" is a fact
and not a hope (invariant 3), and why the whole thing stays correct as it grows (invariant 4). Build
your own loop on top of them.*
