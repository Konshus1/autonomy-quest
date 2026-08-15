"""Ground-truth (code-owned) capability -> consequence-class classification + one-way blast ratchet.

Design: design_authority_envelope.md Slice 1; contract-shaped toward V2 AuthorityPolicy (BB #2716).

WHY THIS EXISTS
---------------
Both the consequence gate (runner.consequence_gate.assess_plan_gate) and the execute-to-learn
predicate (Loop._frame_gaps_are_reversibly_self_observable) derive an action's CONSEQUENCE LEVEL
from the MODEL's self-declared per-step ``blast_radius`` dict. That is a declaration-trust gap: the
governed actor writes its own risk label, so an UNDER-declared external/irreversible action (tagged
reversible / low-blast) passes the gate. "A claim is not authority" -- applied to consequence.

This module adds a deterministic, CODE-OWNED classification the model never authors, and ratchets the
effective level to ``max(model_declared, capability_implied)``. ONE-WAY: the model can only ever
RAISE its blast, never lower it below what its capability implies.

HONESTY ABOUT SIGNAL STRENGTH (this is Slice 1, not the substrate wall of Slice 2)
----------------------------------------------------------------------------------
At GATE TIME the runner does not yet execute through a least-privilege substrate, so there is no
post-hoc syscall / tool-invocation ground truth to read here. The signals available now, strongest
(most independent of the actor's self-labelling) first:

  1. mission boundary ``must_ask_first`` -- MISSION / MANAGER-owned; the actor cannot edit it. This
     is the closest thing to true ground truth in Slice 1. (Matching is by term against the actor's
     free-text ``action``, so the *match* is heuristic even though the policy SOURCE is authoritative
     and non-actor-authored.)
  2. work-level flags ``spends_money`` / ``touches_human`` / ``commits`` -- model-declared, BUT emitted
     on a SEPARATE channel from the per-step ``blast_radius`` dict the gate trusts. Cross-checking
     them catches the exact failure mode (blast dict under-declared while the coarse flag stays
     honest). Not pure ground truth -- a cross-declaration consistency check.
  3. capability keywords in the step ``action`` text -- a CODE-OWNED keyword map applied to actor
     free-text. Independent of every model RISK label (the actor writes ``action`` to say what to do,
     not to label risk), but the actor authors the text, so it is best-effort and evadable.

All three only ever RAISE the level (one-way ratchet); none can lower it. Fail-CLOSED on opaque input
(reuses assess_blast_radius's level-3-on-unknown -- see 2932ff9). Flag-gated by ``AQ_GROUND_TRUTH_BLAST``;
default OFF => this module is never consulted and behavior is byte-identical to pre-fix.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .consequence_gate import assess_blast_radius

POLICY_VERSION = "capability-consequence-v1"

#: Environment flag. Default OFF => callers must not consult this module at all.
FLAG = "AQ_GROUND_TRUTH_BLAST"

#: The high-consequence class. An irreversible / external capability is ALWAYS this level, regardless
#: of any model-declared blast label -- send email / spend money / publish / deploy / external HTTP
#: write / delete-prod / contact a real person.
IRREVERSIBLE_EXTERNAL = 3

#: CODE-OWNED (never model-authored) capability-verb map applied to the step's free-text ``action``.
#: Each entry: (tuple of lowercased substrings, human reason). A match implies IRREVERSIBLE_EXTERNAL.
#: Deliberately specific to avoid false positives on internal reversible work (e.g. aqdb writes).
_EXTERNAL_CAPABILITY_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("send email", "send an email", "send a email", "email to ", "emails to ", "e-mail", "mailto:"),
     "action sends email"),
    (("publish externally", "publish to", "publish a", "post to twitter", "post a tweet", "tweet ",
      "deploy to", "deploy the", "deploy prod", "go live"),
     "action publishes/deploys externally"),
    (("http post", "post request", "-x post", "external api call", "external http", "webhook",
      "fire a request to", "call the external"),
     "action performs an external HTTP write"),
    (("charge the", "charge a", "take payment", "make a payment", "process payment", "purchase ",
      "buy ", "wire ", "send money", "invoice "),
     "action spends money"),
    (("delete prod", "delete production", "drop table", "drop the table", "truncate ", "rm -rf",
      "wipe the", "destroy the production"),
     "action performs a destructive / production deletion"),
    (("phone call to", "call the customer", "call a customer", "text the customer", "contact the customer",
      "contact a real", "reach out to the", "dm the", "message the user"),
     "action contacts a real person"),
)

#: Recognized INTERNAL / reversible capabilities (aqdb curated customer/subscription writes,
#: read-only query). Presence lets the typed verdict report internally_authorized=True; it never
#: LOWERS a level (the ratchet is one-way up), it only labels a low-implication action honestly.
_INTERNAL_CAPABILITY_KEYWORDS: tuple[str, ...] = (
    "aqdb", "query", "read-only", "read only", "select ", "count(", "upsert", "record in the database",
    "subscription record", "customer record", "internal note", "local file", "scratch",
)


@dataclass(frozen=True)
class CapabilityConsequence:
    """Typed ratchet verdict for a single step, shaped toward V2 AuthorityPolicy (BB #2716).

    Additive: this is a NEW parallel accessor; it does not replace GateDecision / BlastAssessment.
    """
    effective_level: int          # max(model_declared_level, capability_level) -- the ratcheted level
    model_declared_level: int     # from assess_blast_radius(step["blast_radius"]) -- what the actor claimed
    capability_level: int         # what the code-owned map implies from the (mostly) non-self signals
    reversible: bool              # effective_level < IRREVERSIBLE_EXTERNAL
    internally_authorized: bool   # capability is a recognized internal one and not external
    low_consequence: bool         # effective_level < gate_level (would NOT trip the human gate)
    capability_class: str         # "irreversible_external" | "internal" | "unknown"
    reasons: tuple[str, ...] = ()
    policy_version: str = POLICY_VERSION


@dataclass(frozen=True)
class CapabilityContext:
    """Work-level, code-observable signals threaded into the gate/predicate when the flag is ON.

    These are the non-per-step-blast signals: the coarse work flags (a SEPARATE declaration channel
    from the per-step blast dict) plus the mission-owned must_ask_first boundary.
    """
    spends_money: bool = False
    touches_human: bool = False
    commits: bool = False
    must_ask_first: tuple = ()


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True iff the ground-truth-blast ratchet is switched on. Default OFF."""
    source = env if env is not None else os.environ
    return str(source.get(FLAG, "")).strip().lower() in {"1", "true", "yes", "on"}


def _must_ask_terms(must_ask_first) -> tuple[str, ...]:
    if isinstance(must_ask_first, str):
        items: Sequence = [must_ask_first]
    elif isinstance(must_ask_first, Sequence):
        items = must_ask_first
    else:
        return ()
    terms = []
    for it in items:
        t = str(it).strip().lower()
        if len(t) >= 3:  # guard against absurd 1-2 char substring matches
            terms.append(t)
    return tuple(terms)


def classify_capability(action, *, spends_money: bool = False, touches_human: bool = False,
                        commits: bool = False, must_ask_first=None) -> tuple[int, str, tuple[str, ...]]:
    """Return (capability_implied_level, capability_class, reasons). CODE-OWNED: reads NO model risk
    label (never the per-step blast dict). Fail-CLOSED: an opaque action (not a str) -> level 3."""
    reasons: list[str] = []
    level = 0

    # (2) work-level flags -- separate declaration channel from the per-step blast dict.
    if spends_money:
        level = IRREVERSIBLE_EXTERNAL
        reasons.append("work.spends_money=True (spends money)")
    if touches_human:
        level = IRREVERSIBLE_EXTERNAL
        reasons.append("work.touches_human=True (contacts a real person)")
    if commits:
        level = IRREVERSIBLE_EXTERNAL
        reasons.append("work.commits=True (external durable commit / publish)")

    if not isinstance(action, str):
        # Opaque capability -- we cannot see what it does. Fail CLOSED, never open.
        return (IRREVERSIBLE_EXTERNAL, "irreversible_external",
                tuple(reasons) + ("opaque action (non-string) -> fail-closed level 3",))

    text = action.lower()

    # (3) code-owned capability-verb scan of the actor's free-text action.
    for needles, reason in _EXTERNAL_CAPABILITY_KEYWORDS:
        if any(n in text for n in needles):
            level = IRREVERSIBLE_EXTERNAL
            reasons.append(f"{reason} (capability keyword)")
            break

    # (1) mission-owned must_ask_first boundary -- strongest non-actor-authored signal.
    for term in _must_ask_terms(must_ask_first):
        if term in text:
            level = IRREVERSIBLE_EXTERNAL
            reasons.append(f"action matches mission must_ask_first boundary {term!r}")
            break

    if level >= IRREVERSIBLE_EXTERNAL:
        return level, "irreversible_external", tuple(reasons)
    if any(k in text for k in _INTERNAL_CAPABILITY_KEYWORDS):
        return level, "internal", tuple(reasons)
    return level, "unknown", tuple(reasons)


def effective_blast(step, *, spends_money: bool = False, touches_human: bool = False,
                    commits: bool = False, must_ask_first=None,
                    gate_level: int = IRREVERSIBLE_EXTERNAL) -> CapabilityConsequence:
    """The one-way ratchet for a single plan step: effective = max(model_declared, capability_implied).

    model_declared comes from the CANONICAL assess_blast_radius (so we never rate a step lower than
    the existing gate would, and inherit its fail-closed level-3-on-opaque behavior). capability comes
    from classify_capability (code-owned, non-self signals). The model can only RAISE, never lower.
    """
    is_map = isinstance(step, Mapping)
    model_level = assess_blast_radius(step.get("blast_radius") if is_map else None).level
    action = step.get("action") if is_map else None
    cap_level, cap_class, cap_reasons = classify_capability(
        action, spends_money=spends_money, touches_human=touches_human,
        commits=commits, must_ask_first=must_ask_first,
    )
    effective = max(model_level, cap_level)
    reasons: list[str] = []
    if model_level:
        reasons.append(f"model-declared blast level {model_level}")
    reasons.extend(cap_reasons)
    return CapabilityConsequence(
        effective_level=effective,
        model_declared_level=model_level,
        capability_level=cap_level,
        reversible=effective < IRREVERSIBLE_EXTERNAL,
        internally_authorized=(cap_class == "internal" and cap_level < IRREVERSIBLE_EXTERNAL),
        low_consequence=effective < gate_level,
        capability_class=cap_class,
        reasons=tuple(reasons),
    )
