# Close-the-loop gate: grounded-evaluator design

**Status:** design / follow-up to the landed shadow runtime (public main `0ea52fe`).
**Supersedes** the "take test execution away from the candidate" framing in
`hermetic_verifier/HERMETIC_CENSUS_LIMITATION.md` as the path to an armable gate.
**Origin:** design conversation, 11 Aug 2026 (task #4834).

## The problem this solves

The hermetic sandbox soundly proves **powerlessness** (the candidate cannot push,
exfiltrate, escape, or reach credentials) but it cannot prove **test authenticity**:
the candidate supplies the very code that runs its own tests (pytest imports the
candidate's files), and the sandbox cannot drop the candidate to a lower-privilege
uid than the census authority without the root it deliberately lacks (`setuid`
returns EPERM under cap-drop-ALL / no-new-privileges). A determined candidate can
therefore forge a passing test census (see `HERMETIC_CENSUS_LIMITATION.md`). Every
attempt to make the census unforgeable *mechanically, in-process* reintroduced the
same vacuous-green hole by a new channel (rc=0 → stdout-regex → fd-delivery). It is
provably not closable inside a single-process, rootless sandbox.

## The reframe

Stop asking the sandbox to prove authenticity. Split the verdict into two
independent guarantees, each doing only what it can actually prove:

1. **Powerlessness — mechanical, sound.** Keep the hermetic sandbox exactly as-is
   for the guarantee it *does* prove: the candidate is inert (six-capability
   BLOCKED-in-sandbox / ALLOWED-naive contrast, full verdict binding, secrecy). Its
   test-pass signal is treated as *advisory input to the evaluator*, never as
   authority.

2. **Authenticity + warrant — semantic, judged, but grounded.** An independent,
   de-correlated evaluator (different model/harness, **read-only, never executes
   candidate code**) decides whether the work is real and warranted. It is not
   fooled by a forged census because it does not trust a self-count — it reads the
   real artifacts and checks them against ground truth the candidate cannot author.

The verdict requires **both**. The public actuator stays inert until both are green
*and* the actuator refusal proof is green in both directions.

## What the evaluator reads, and what it trusts

| Input | Source | Trust |
|---|---|---|
| The diff | git object | ground truth (candidate cannot lie about what's committed) |
| The tests | git object | ground truth |
| The worker's reasoning | worker-emitted | a **claim**, checked against the artifacts — never believed on its own |
| **The learning reference** | links to prior recorded experiences, retrieved from the system's own record | **ground truth the candidate cannot fabricate** (see below) |

All candidate-supplied text (diff, tests, reasoning, comments, docstrings) is read
as **untrusted data, never as instructions** — the evaluator is hardened against
prompt-injection through the artifacts it reads.

## The three checks

### 1. Discriminate / revert check (semi-mechanical, red-first built in)
Do the supplied tests **fail when the change is reverted**? Tests that pass with and
without the change do not test the change; a zero-real-tests forgery fails this
trivially. This runs inside the powerlessness sandbox (running tests is already
contained), but the *structure* it verifies — fails-on-revert — is far harder to
forge than a pass count, and read alongside the actual test source it is near
impossible to fake convincingly. This is the red-first discipline, moved from the
manager into the gate.

### 2. Grounding check (the strong one)
When the change is a **learning** (a correction-driven fix, a principle-grounded
change, a self-improvement), it carries a **learning reference** linking the
specific prior experiences that generated it. The evaluator **retrieves those
experiences** and verifies:
- they **exist** in the system's record and are owned by a **separate principal**
  (the candidate did not and cannot author them);
- they **actually support** the change (the diff does what the cited experience
  implies is needed);
- the generalization is **warranted** — it does not over-generalize from a single
  instance, and does not contradict sibling experiences under the same generating
  rule.

This is the counterexample-generalization / *learn-the-type-not-the-instance*
honesty gate from the self-correction consultant — now **enforceable at the gate**,
because the instances are retrievable. The forgery attack dies quietly: a
zero-real-work candidate has no genuine experience to cite, and a fabricated
citation dangles against a record it cannot write.

### 3. Artifact-vs-claim check
Does the diff actually do what the reasoning claims? Do the tests exercise the
changed code paths? Standard read-the-diff review, with the reasoning as the claim
under audit.

## Why grounding is the anchor (and why this is not a new system)

The candidate controls the diff, the tests, and the narrative. It does **not**
control the prior experiences the learning must cite — those were written when the
experience happened and are owned by a different principal. This is exactly the
**governance grounding** model already built and deployed: a causal principle is
promoted only if it is grounded in evidence a separate `aq_evaluator` principal
owns, and grounding-cannot-be-faked. The close-the-loop evaluator is the same move,
applied to a work product instead of a principle. The infrastructure largely
exists; this design reuses it rather than inventing it.

**The unification:** governance grounding, self-correction's counterexample
evidence, and this evaluator gate are one mechanism — *a learning is valid only
against the experience it is grounded in, verified by someone who did not author
it.* This design extends that from principles to the close-the-loop verdict.

## Requirements and the one wire to verify

- **The worker must propagate the learning reference onto its work product**
  (branch/commit), linking the generating experiences. This exists for
  principle-grounded and correction-driven work via the reference event; **verify
  the close-the-loop worker actually carries it end-to-end and does not drop it.**
  This is the single wire to check — not a new system to build.
- **Non-learning changes** (ordinary "implement feature X" with no generating
  experience) legitimately have no grounding to check; they fall back to the
  discriminate + artifact checks. That is expected, not a gap.
- **Evaluator independence:** different model/harness from the worker, read-only, no
  execution of candidate code, injection-hardened.

### Grounding integration preconditions (the evaluator MUST honor)

The propagation + retrieval/verification library lives in
`runner/close_loop/learning_reference.py` (`LEARNING_REFERENCE.md`). It is a pure
grounding checker; three preconditions are load-bearing and are the evaluator's
responsibility, not the library's:

- **Trusted store.** `verify_learning_reference` takes an `ExperienceStore`
  (a caller-wired `Protocol`; the `isinstance` guard is only structural). The
  evaluator MUST pass a **trusted** store reading `schema/022`+`schema/025` under a
  principal the candidate does not control. A candidate-influenced store voids
  grounding entirely.
- **Authenticated candidate identity.** `verify_learning_reference(...,
  *, authenticated_candidate_principal)` requires the candidate's identity from
  **trusted evaluator context** (spawn record / lease owner), never from the
  reference or commit-trailer payload. The separate-principal check compares the
  store's owner against this authenticated identity; the self-declared
  `candidate_principal` is untrusted and only cross-checked. (This closes a
  demonstrated self-grounding forge where a candidate rewrote its own declared
  identity — or a lexical/Unicode variant of it — to dodge the check.)
- **`NO_REFERENCE` is not a pass.** A change that CLAIMS to be a learning
  (correction-driven / principle-grounded / self-improvement) MUST be rejected by
  the evaluator if its grounding verdict is `NO_REFERENCE`. Empty references are
  legitimate only for ordinary non-learning changes, which then fall back to the
  discriminate + artifact checks.

## Threat-model note

The hermetic sandbox was built for a fully-adversarial *external* attacker. The
close-the-loop candidate is a *fleet worker* — a subagent we spawned, far more
likely buggy or confused than deliberately malicious. The mechanical census forgery
requires deliberate malice; the grounded evaluator is a strong, appropriate gate
for the realistic threat, and the powerlessness sandbox contains the worst case
regardless. Match the paranoia to the actual threat: powerlessness is the hard
floor; grounded-evaluator warrant is the working gate.

## Verdict shape

```
verdict = powerlessness(hermetic, sound)
      AND discriminate(tests fail on revert)
      AND grounded_warrant(evaluator: learning ↔ linked experiences, de-correlated, read-only)
      AND artifact_matches_claim(evaluator)
# public actuator armed only when the above hold AND refusal proof green both ways.
```
