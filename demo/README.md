# Causal Learning Loop — watch an agent think, with receipts

A self-contained, dependency-light demo of the mechanism at the heart of
Autonomy Quest: an agent that **mines a causal principle from its own completed
runs**, **predicts an outcome before it acts**, and then **earns or loses your
trust as the real number moves** — honestly, capped, and with a paper trail.

No container. No database. No API. No keys. It runs the *actual* causal engine
from `ralph_portable` (the same code the production loop calls) via the
`InMemoryCausalEdgeStore`, against a small scripted mission so the whole thing
plays out in one terminal in under a second.

## Why this matters

Most "AI that learns" is a black box: it changes its behavior and you're asked to
trust that something reasonable happened. This loop is the opposite — it is
**legible and honest by construction**:

- **It shows its reasoning before it acts.** Every cycle prints a pre-act
  prediction ("~30% sure this moves the number") drawn from a stored causal
  model, not a vibe.
- **It starts fuzzy and stays honest.** A principle mined from experience is
  capped at 34% certainty — a hunch can *never* masquerade as a guarantee.
- **Trust is earned, not asserted.** A principle only climbs a rung
  (`fuzzy → evidential → formal`) after 5+ confirming outcomes, and even then a
  promotion is a *gated proposal* a separate loop must actuate — nothing
  self-promotes.
  > **Shipped-vs-demo honesty:** in the *shipped* Autonomy Quest today the loop
  > only PROPOSES promotions/demotions — it does **not** apply them (every live
  > edge stays `fuzzy`; actuation is roadmap). This demo actuates the proposals
  > itself, standing in for that future learning-loop step, so you can watch the
  > *whole* designed ladder in one run. What's live is mine → consult → predict →
  > score → earn-support; the promotion you see actuated here is illustrative.
- **It refuses to over-claim.** A judgment-based principle is *flatly refused*
  promotion to "formal/guaranteed," because a guarantee needs a script or
  predicate, not an LLM's opinion.
- **Demotion is fast.** A "guaranteed" automation that reality breaks is
  demoted the moment the number disagrees — no grace period for confident wrong.
- **Every claim has receipts.** Each mined principle carries provenance back to
  the specific runs and learnings that produced it.

That's the pitch to a skeptic: *you can watch it think, and you can audit every
number it prints.*

## What's real vs. simulated

| Real (imported from `ralph_portable`, not re-implemented) | Simulated (this demo only) |
|---|---|
| `principle_mining.mine_causal_edges` — mines fuzzy edges + provenance | The mission outcomes (did a tactic move replies this week) |
| `InMemoryCausalEdgeStore` — stores, scores plans, records evidence | Scripted so the trace is deterministic and reproducible |
| `causal_edges.surprise` — prediction-vs-actual + learning signal | |
| `causal_edges.propose_update` — the gated promote/demote decision | |
| `causal_edges.is_guaranteed` — the "formal + script" guarantee test | |

The causal *reasoning* is production code. Only the *world* it reasons about is a
stand-in, so you can watch the mechanism instead of waiting on a live mission.

## How to run

From the repo root (needs only Python 3.11+ stdlib and the repo's
`ralph_portable`; no install, no env vars):

```bash
python3 demo/causal_learning_demo.py
```

## What you'll see (real output)

This is the actual, unedited output of the script above.

```
==========================================================================
         AUTONOMY QUEST -- CAUSAL LEARNING LOOP  (watch it think)
==========================================================================

Mission     : grow weekly REPLIES to founder outreach
The number  : replies / week  (starts at 14)
The rule    : the agent may PREDICT and ADVISE, never silently promote a
              hunch to 'guaranteed'. Trust is earned, capped, and receipted.

The three dials on every causal claim:
   formality  = how sure the cause->effect is  : fuzzy < evidential < formal
   strictness = how hard it binds behavior     : advisory < soft < hard
   directness = how deterministic the action is: judgment < predicate < script

==========================================================================
          ACT 1  --  MINE A PRINCIPLE FROM THE AGENT'S OWN RUNS
==========================================================================

Feeding 4 completed runs into the miner. It keeps only runs that
SUCCEEDED and where the number actually MOVED (honest evidence only):

   run #101  send_personalized_followup 8->11  (moved +3)
   run #102  send_personalized_followup 11->13  (moved +2)
   run #103  send_personalized_followup 13->14  (moved +1)
   run #104  blast_generic_template     14->14  (no move -> IGNORED)

Miner returned 1 candidate principle(s). Here is the one it found:

--------------------------------------------------------------------------
  PRINCIPLE:  send_personalized_followup
              ->  measure_up   (the weekly number goes up)
  DIALS    :  formality=fuzzy      strictness=advisory  directness=judgment
  CLAIM    :  '~30% sure this cause moves the number'
              (fuzzy is capped at 34% by design -- a hunch can NEVER
               masquerade as a formal guarantee)
  MINED    :  True   observed over 3 distinct runs
  PROVENANCE (receipts -- every claim traces to a real run + learning):
     - run #101 / learning #9101: Replies jump when the follow-up names the prospect's last shipped feature.
     - run #102 / learning #9102: A single concrete question in the P.S. line pulls replies.
     - run #103 / learning #9103: Tuesday-morning sends out-reply Friday sends.
--------------------------------------------------------------------------

Note the honesty: the generic-blast run was DROPPED (the number never
moved), so it earned no principle. Mining only ever proposes fuzzy guides.

==========================================================================
              ACT 2  --  PREDICT BEFORE ACTING, THEN EARN IT
==========================================================================

==========================================================================
CYCLE 1   plan: send_personalized_followup -> measure_up
==========================================================================
  PRE-ACT PREDICTION : '~30% sure this moves the number'
                       (rung=fuzzy, support so far=0, guaranteed=False)
  ACT                : agent runs it -- named their last release in the opener
  OUTCOME            : replies 15 -> 17  (the number MOVED UP)
  SURPRISE           : predicted=0.3 actual=1.0 |gap|=0.7  -> signal='confirm'
  SUPPORT            : 1   (a 'confirm' earns +1 toward promotion; a miss does not)
  GATED PROPOSAL     : HOLD (fuzzy -> fuzzy)  --  insufficient support or unresolved surprise

  ... [cycles 2-4 accumulate confirming support: 2, 3, 4] ...

==========================================================================
CYCLE 5   plan: send_personalized_followup -> measure_up
==========================================================================
  PRE-ACT PREDICTION : '~30% sure this moves the number'
                       (rung=fuzzy, support so far=4, guaranteed=False)
  ACT                : agent runs it -- tied the ask to their public roadmap
  OUTCOME            : replies 21 -> 23  (the number MOVED UP)
  SURPRISE           : predicted=0.3 actual=1.0 |gap|=0.7  -> signal='confirm'
  SUPPORT            : 5   (a 'confirm' earns +1 toward promotion; a miss does not)
  GATED PROPOSAL     : PROMOTE (fuzzy -> evidential)  --  5 supporting observations
  >> LOOP ACTUATES   : PROMOTED to 'evidential'. Ceiling lifts; next prediction rises.

==========================================================================
CYCLE 6   plan: send_personalized_followup -> measure_up
==========================================================================
  PRE-ACT PREDICTION : '~67% sure this moves the number'
                       (rung=evidential, support so far=5, guaranteed=False)
  ACT                : agent runs it -- short, specific, one CTA
  OUTCOME            : replies 23 -> 24  (the number MOVED UP)
  SURPRISE           : predicted=0.67 actual=1.0 |gap|=0.33  -> signal='confirm'
  SUPPORT            : 6   (a 'confirm' earns +1 toward promotion; a miss does not)
  GATED PROPOSAL     : HOLD (evidential -> evidential)  --  cannot promote to formal while directness=judgment (needs script/predicate)
  >> LOOP ACTUATES   : REFUSED. It earned the support, but a judgment
                       call can NEVER be promoted to 'formal/guaranteed'
                       -- that needs a script or predicate, not a hunch.

==========================================================================
CYCLE 7   plan: send_personalized_followup -> measure_up
==========================================================================
  PRE-ACT PREDICTION : '~67% sure this moves the number'
                       (rung=evidential, support so far=6, guaranteed=False)
  ACT                : agent runs it -- audience fatigued -- same play stopped landing
  OUTCOME            : replies 24 -> 24  (NO MOVE -- reality disagreed)
  SURPRISE           : predicted=0.67 actual=0.0 |gap|=0.67  -> signal='investigate'
  SUPPORT            : 6   (a 'confirm' earns +1 toward promotion; a miss does not)
  GATED PROPOSAL     : HOLD (evidential -> evidential)  --  insufficient support or unresolved surprise
  >> LOOP ACTUATES   : HELD FOR INVESTIGATION. The principle never
                       claimed enough certainty to be 'confidently
                       wrong', so a miss is logged, not auto-demoted.

==========================================================================
     ACT 3  --  THE FAST-DEMOTE LANE (nothing stays trusted for free)
==========================================================================

The mined hunch above could never be 'confidently wrong' -- it never
claimed formal certainty. But a hand-authored, GUARANTEED automation can.
Watch what happens to one when reality breaks it.

  PRINCIPLE : apply_verified_coupon -> measure_up
  DIALS     : formality=formal     strictness=soft      directness=script
  PRE-ACT   : '~95% sure' (guaranteed=True -- a formal claim run by a script)
  ACT       : runs it -- but the coupon vendor changed their API overnight
  OUTCOME   : the number did NOT move  (a CONFIDENT prediction, WRONG)
  SURPRISE  : predicted=0.95 actual=0.0 -> signal='demote'  (p>=0.70 + wrong = the fast-demote lane)
  PROPOSAL  : DEMOTE (formal -> evidential)  --  confident prediction, wrong outcome
  >> ACTUATE: DEMOTED to 'evidential'. Certainty ceiling drops ~95% -> ~67%, guaranteed=False.

  Promotion is slow and earned (needs 5+ confirms, and gated). Demotion
  on counter-evidence is FAST. Even a 'guaranteed' script is humbled the
  moment the real number disagrees.

==========================================================================
                WHAT JUST HAPPENED  --  THE HONEST LEDGER
==========================================================================

  Hero principle : send_personalized_followup -> measure_up
  Final rung     : evidential  (earned up from 'fuzzy', refused 'formal')
  Support earned : 6 confirming outcomes
  Evidence kept  : 7 scored surprises on file
  Guaranteed?    : False   <-- still NO, and that is the point

  Every number above is real output of ralph_portable's causal engine:
    - The principle is FUZZY: mined from its own runs, capped honest, with
      provenance you can click back to a run and a learning.
    - Trust is EARNED, not guaranteed: it climbed a rung only after 5
      confirming outcomes, and was flatly refused a 'formal' guarantee
      because it is still a judgment call.
    - It was NEVER ACTED ON autonomously: at every cycle it only PREDICTED
      and ADVISED; a separate loop actuated each gated promote/demote.

  That is the whole pitch: an agent that shows its causal reasoning before
  it acts, then earns or loses your trust as the real number moves -- with
  receipts, and without ever quietly promoting a hunch to a guarantee.
```

*(Cycles 2–4 are elided above for brevity; the script prints them in full.)*

## The honest fine print

Everything the demo claims is enforced by the engine, not by the narration:

- **34% fuzzy cap** — `principle_mining._FUZZY_CAP` and `causal_edges._FORMALITY_WEIGHT`.
- **A mined principle can never be "confidently wrong"** — `surprise()` only emits
  the fast-`demote` signal when the stated certainty is ≥ 0.70, and the evidential
  ceiling is 0.67. A fuzzy/evidential *judgment* principle physically cannot claim
  enough certainty to trip that lane, so its misses go to `investigate` (held), not
  auto-demote. That's why Cycle 7 is an investigation, not a demotion.
- **Gated promotion / refused formal** — `propose_update()` requires
  `support_count ≥ 5` and refuses `→ formal` while `directness == "judgment"`.
- **Nothing self-promotes** — the store only ever *returns a proposal*; the demo,
  standing in for the learning loop, is what actuates it (exactly as
  `runner/causal_sync.py` describes the real loop consulting and recording).

## Files

- `demo/causal_learning_demo.py` — the runnable, narrated demo (stdlib + `ralph_portable` only).
- `demo/README.md` — this file.
