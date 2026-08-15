"""Declarative capability ENVELOPE: operator-editable authority over capability CLASSES.

Design: design_authority_envelope.md Slice 2; contract-shaped toward V2 AuthorityPolicy (BB #2716).

OPERATOR DECISION (Kevin 2026-08-15)
------------------------------------
GATED BY DEFAULT; the operator can OPEN gates; gates are EASILY configurable (declarative, no code
change); the actor can only REQUEST an opening (Slice 3), never self-open. Slice 1 proved that a
NUMERIC blast level alone is the wrong authority when the operator wants a whole CLASS of capability
held for a human regardless of how small any single action looks. This slice adds the CATEGORICAL
gate: a capability CLASS the envelope marks ``gated`` escalates to a human REGARDLESS of the numeric
blast level; a class the operator has explicitly OPENED falls through to the existing numeric gate
(the amended BB #878 stance -- numeric-only -- becomes the behaviour exactly where the operator
opted into it).

WHAT IS REAL BINDING vs DECLARATION-DERIVED (honest -- read before trusting a class)
------------------------------------------------------------------------------------
Strongest (most independent of the actor's own risk self-labelling) first:

  1. mission ``must_ask_first`` -- MISSION/OPERATOR-authored, the actor cannot edit it. A match here
     ALWAYS gates categorically and CANNOT be opened by the envelope: the mission boundary is a
     separate operator instrument that says "ask first", and the finer envelope must never silently
     override the coarser explicit boundary. (The MATCH is a term-in-free-text heuristic even though
     the SOURCE is authoritative.)
  2. work-level flags ``spends_money`` / ``touches_human`` / ``commits`` -- model-declared, BUT on a
     SEPARATE channel from the per-step blast dict, and they map to the real high-consequence classes
     spend_money / contact_human / commit. A genuine cross-declaration binding, not pure ground truth.
  3. capability keywords in the step ``action`` free-text -- a CODE-OWNED keyword map. Independent of
     every model RISK label but the actor authors the text, so it is BEST-EFFORT and evadable. It is
     RAISE-ONLY: it can add a gated class, never remove one, and it is never the sole authority that
     OPENS anything.

THE SUBSTRATE WALL IS A DOCUMENTED FOLLOW-UP, NOT THIS SLICE. None of the above is the true wall:
the wall is removing from the actor's runtime the capabilities the envelope does not open (the
container's least-privilege role/tools), so an over-eager actor physically cannot exceed the
envelope. Today the ACT phase runs inside a container whose ``aq_actor`` role is scoped to the
curated customers/subscriptions surface (see LIMITATIONS in the build note); there is no email /
payment / external-publish tool in the image at all, so for the STANDARD business template the
external classes are already substrate-absent. But this module does not yet READ that role to derive
the class -- it derives from the declaration channels above. The envelope is the POLICY object a
future substrate wall (and the Slice-3 open-request) will target; the gate here is the backstop.

Fail-CLOSED everywhere: an unknown/opaque action, an unspecified class, an unknown config value ->
gated. Flag-gated by ``AQ_AUTHORITY_ENVELOPE``; default OFF => this module is never consulted and the
gate is byte-identical to Slice-1-flag-off behaviour.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

POLICY_VERSION = "authority-envelope-v1"

#: Environment flag. Default OFF => callers must not consult this module at all.
FLAG = "AQ_AUTHORITY_ENVELOPE"

# -- capability classes -------------------------------------------------------------------------
CLASS_SPEND_MONEY = "spend_money"
CLASS_CONTACT_HUMAN = "contact_human"
CLASS_COMMIT = "commit"
CLASS_PUBLISH_EXTERNAL = "publish_external"
CLASS_DEPLOY = "deploy"
CLASS_EXTERNAL_WRITE = "external_write"
CLASS_DELETE_PROD = "delete_prod"
CLASS_INTERNAL_REVERSIBLE = "internal_reversible"

#: The high-consequence set -- GATED by default. An operator opens one by naming it ``open``.
HIGH_CONSEQUENCE_CLASSES: tuple[str, ...] = (
    CLASS_SPEND_MONEY, CLASS_CONTACT_HUMAN, CLASS_COMMIT, CLASS_PUBLISH_EXTERNAL,
    CLASS_DEPLOY, CLASS_EXTERNAL_WRITE, CLASS_DELETE_PROD,
)
#: Every recognized class. A config naming any class NOT in here fails closed (loud error).
ALL_CLASSES: frozenset[str] = frozenset(HIGH_CONSEQUENCE_CLASSES) | {CLASS_INTERNAL_REVERSIBLE}

DISPOSITION_OPEN = "open"
DISPOSITION_GATED = "gated"
_DISPOSITIONS = frozenset({DISPOSITION_OPEN, DISPOSITION_GATED})

#: Default disposition per class: high-consequence GATED, internal_reversible OPEN.
_DEFAULT_DISPOSITIONS: dict[str, str] = {
    **{c: DISPOSITION_GATED for c in HIGH_CONSEQUENCE_CLASSES},
    CLASS_INTERNAL_REVERSIBLE: DISPOSITION_OPEN,
}


class EnvelopeConfigError(ValueError):
    """instance.yaml's authority_envelope names an unknown class or an unknown disposition.

    Fail LOUD, never silently ignore: a typo'd class name that was silently dropped would leave the
    operator believing they had opened (or closed) a gate they did not touch."""


# -- capability classification ------------------------------------------------------------------
#: CODE-OWNED (never model-authored) capability-verb map applied to the step's free-text ``action``.
#: Each entry: (tuple of lowercased substrings, capability class, human reason). RAISE-ONLY,
#: BEST-EFFORT (the actor authors the text): a match ADDS its class; it never opens anything.
_CLASS_KEYWORDS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("send email", "send an email", "send a email", "email to ", "emails to ", "e-mail", "mailto:",
      "phone call to", "call the customer", "call a customer", "text the customer",
      "contact the customer", "contact a real", "reach out to the", "dm the", "message the user"),
     CLASS_CONTACT_HUMAN, "action contacts a real person"),
    (("publish externally", "publish to", "publish a", "post to twitter", "post a tweet", "tweet ",
      "export to", "share externally"),
     CLASS_PUBLISH_EXTERNAL, "action publishes externally"),
    (("deploy to", "deploy the", "deploy prod", "ship to prod", "go live", "release to production"),
     CLASS_DEPLOY, "action deploys"),
    (("http post", "post request", "-x post", "external api call", "external http", "webhook",
      "fire a request to", "call the external", "put request", "external write"),
     CLASS_EXTERNAL_WRITE, "action performs an external HTTP write"),
    (("charge the", "charge a", "take payment", "make a payment", "process payment", "purchase ",
      "buy ", "wire ", "send money", "invoice "),
     CLASS_SPEND_MONEY, "action spends money"),
    (("delete prod", "delete production", "drop table", "drop the table", "truncate ", "rm -rf",
      "wipe the", "destroy the production"),
     CLASS_DELETE_PROD, "action performs a destructive / production deletion"),
    (("git commit", "commit to the repo", "commit and push", "push to origin", "push the branch",
      "merge to main", "merge into main"),
     CLASS_COMMIT, "action makes a durable external commit"),
)

#: Recognized INTERNAL / reversible capabilities (aqdb curated customer/subscription writes,
#: read-only query, local scratch). Presence lets the action be classed internal_reversible; it is
#: NEVER a signal that opens a high-consequence class (raise-only asymmetry preserved).
_INTERNAL_KEYWORDS: tuple[str, ...] = (
    "aqdb", "query", "read-only", "read only", "select ", "count(", "upsert",
    "record in the database", "subscription record", "customer record", "internal note",
    "local file", "scratch",
)


@dataclass(frozen=True)
class AuthorityEnvelope:
    """The operator-declared authority envelope: capability class -> ``open`` | ``gated``.

    Constructed from instance.yaml (``authority_envelope:``) via :func:`from_config`, which fills
    every unspecified class with its default (high-consequence GATED, internal_reversible OPEN) and
    fails LOUD on an unknown class or value. Frozen: the actor cannot mutate it -- it is the thing a
    Slice-3 open-REQUEST targets, never something the actor edits directly."""
    dispositions: Mapping[str, str]

    def disposition(self, capability_class: str) -> str:
        """Fail-CLOSED: any class not explicitly OPEN is treated as gated."""
        return DISPOSITION_OPEN if self.dispositions.get(capability_class) == DISPOSITION_OPEN \
            else DISPOSITION_GATED

    def is_gated(self, capability_class: str) -> bool:
        return self.disposition(capability_class) == DISPOSITION_GATED

    def as_dict(self) -> dict[str, str]:
        return dict(self.dispositions)


#: The default envelope used when the section is absent AND wherever a class is unspecified.
#: The dispositions are wrapped read-only (MappingProxyType) so this safety-critical shared singleton
#: cannot be mutated in place by a future bug (defense-in-depth; the frozen dataclass already blocks
#: attribute reassignment). from_config always builds a FRESH dict, so operator config is unaffected.
DEFAULT_ENVELOPE = AuthorityEnvelope(MappingProxyType(dict(_DEFAULT_DISPOSITIONS)))


def from_config(raw) -> AuthorityEnvelope:
    """Parse an ``authority_envelope:`` mapping into an :class:`AuthorityEnvelope`.

    ``raw`` is the value under the ``authority_envelope`` key (None/absent => full defaults). Every
    class the operator does NOT name keeps its default disposition. Fail LOUD (EnvelopeConfigError)
    on an unknown class name or an unknown disposition value -- never silently ignore a typo."""
    dispositions = dict(_DEFAULT_DISPOSITIONS)
    if raw is None:
        return AuthorityEnvelope(dispositions)
    if not isinstance(raw, Mapping):
        raise EnvelopeConfigError(
            f"authority_envelope must be a mapping of capability-class -> open|gated, got {type(raw).__name__}")
    for name, value in raw.items():
        cls = str(name).strip()
        if cls not in ALL_CLASSES:
            raise EnvelopeConfigError(
                f"authority_envelope names unknown capability class {name!r}; "
                f"known classes: {sorted(ALL_CLASSES)}")
        disp = str(value).strip().lower()
        if disp not in _DISPOSITIONS:
            raise EnvelopeConfigError(
                f"authority_envelope[{name!r}] must be 'open' or 'gated', got {value!r}")
        dispositions[cls] = disp
    return AuthorityEnvelope(dispositions)


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True iff the authority-envelope categorical gate is switched on. Default OFF."""
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


@dataclass(frozen=True)
class CapabilityClassification:
    """The capability classes an action falls into, with provenance.

    ``classes`` is the set of recognized classes (may be several -- an action can both spend money
    and contact a human). ``must_ask_first_hit`` is True when the action matched a mission boundary
    (categorical gate that the envelope cannot open). ``fail_closed`` is True when the action was
    opaque (non-string) -- gated regardless of envelope."""
    classes: tuple[str, ...]
    must_ask_first_hit: bool
    fail_closed: bool
    reasons: tuple[str, ...]


def classify_capability_classes(
    action, *, spends_money: bool = False, touches_human: bool = False, commits: bool = False,
    must_ask_first=None,
) -> CapabilityClassification:
    """Map an action + its code-observable context to capability CLASSES. CODE-OWNED: reads NO model
    risk label (never the per-step blast dict). Signals ranked strongest-first in the module docstring.
    Fail-CLOSED: an opaque (non-string) action is flagged ``fail_closed`` (the gate treats it as gated)."""
    classes: set[str] = set()
    reasons: list[str] = []

    # (2) REAL binding: work flags -- separate declaration channel, mapped to real classes.
    if spends_money:
        classes.add(CLASS_SPEND_MONEY)
        reasons.append("work.spends_money=True -> spend_money [declaration-channel binding]")
    if touches_human:
        classes.add(CLASS_CONTACT_HUMAN)
        reasons.append("work.touches_human=True -> contact_human [declaration-channel binding]")
    if commits:
        classes.add(CLASS_COMMIT)
        reasons.append("work.commits=True -> commit [declaration-channel binding]")

    if not isinstance(action, str):
        # Opaque capability -- we cannot see what it does. Fail CLOSED.
        return CapabilityClassification(
            tuple(sorted(classes)), must_ask_first_hit=False, fail_closed=True,
            reasons=tuple(reasons) + ("opaque action (non-string) -> fail-closed gate",))

    text = action.lower()

    # (3) BEST-EFFORT, RAISE-ONLY keyword scan of the actor's free-text action.
    for needles, cls, reason in _CLASS_KEYWORDS:
        if any(n in text for n in needles):
            classes.add(cls)
            reasons.append(f"{reason} -> {cls} [keyword heuristic; best-effort]")

    # (1) OPERATOR boundary: mission must_ask_first. Strongest non-actor-authored signal; a match
    # gates categorically and the envelope cannot open it.
    must_ask_hit = False
    for term in _must_ask_terms(must_ask_first):
        if term in text:
            must_ask_hit = True
            reasons.append(f"action matches mission must_ask_first boundary {term!r} "
                           f"[operator-authored; not envelope-openable]")
            break

    # Internal/reversible only when NOTHING high-consequence matched (asymmetry: internal never
    # cancels a high-consequence class, it only labels an otherwise-benign action).
    if not classes and any(k in text for k in _INTERNAL_KEYWORDS):
        classes.add(CLASS_INTERNAL_REVERSIBLE)
        reasons.append("recognized internal/reversible capability -> internal_reversible")

    return CapabilityClassification(
        tuple(sorted(classes)), must_ask_first_hit=must_ask_hit, fail_closed=False,
        reasons=tuple(reasons))


@dataclass(frozen=True)
class EnvelopeVerdict:
    """Typed categorical-envelope verdict, shaped toward the V2 AuthorityPolicy JSON (BB #2716).

    ``gated`` is the categorical decision: True => escalate to a human REGARDLESS of numeric blast.
    ``gated_classes`` are the specific classes that forced it. When ``gated`` is False the action
    falls through to the existing numeric gate (an OPEN class, or no high-consequence class)."""
    gated: bool                       # categorical: escalate to human regardless of numeric blast
    classes: tuple[str, ...]          # every capability class the action fell into
    gated_classes: tuple[str, ...]    # the classes (or boundary) that forced the categorical gate
    open_classes: tuple[str, ...]     # high-consequence classes the operator has OPENED
    must_ask_first_hit: bool
    fail_closed: bool
    reasons: tuple[str, ...] = ()
    #: SUBSTRATE BINDING (design_authority_envelope.md, substrate_manifest.py): high-consequence
    #: classes the step CLAIMED (via a declaration channel) that are STRUCTURALLY ABSENT from the
    #: substrate -- the plan wants a capability it cannot have. A RED FLAG: held for review, never
    #: silently passed. Empty unless a substrate manifest was supplied (flag AQ_SUBSTRATE_BINDING).
    substrate_mismatch_classes: tuple[str, ...] = ()
    #: True iff a substrate manifest was consulted for this verdict (honest provenance marker).
    substrate_bound: bool = False
    policy_version: str = POLICY_VERSION

    def to_authority_policy(self) -> dict:
        """V2 AuthorityPolicy-shaped dict (BB #2716)."""
        return {
            "verdict": "gated" if self.gated else "open",
            "categorically_gated": self.gated,
            "capability_classes": list(self.classes),
            "gated_classes": list(self.gated_classes),
            "open_classes": list(self.open_classes),
            "must_ask_first_hit": self.must_ask_first_hit,
            "fail_closed": self.fail_closed,
            "substrate_mismatch_classes": list(self.substrate_mismatch_classes),
            "substrate_bound": self.substrate_bound,
            "reasons": list(self.reasons),
            "policy_version": self.policy_version,
        }


def assess_action_envelope(
    action, envelope: AuthorityEnvelope, *, spends_money: bool = False, touches_human: bool = False,
    commits: bool = False, must_ask_first=None, manifest=None,
) -> EnvelopeVerdict:
    """The categorical envelope decision for a single action.

    GATED (categorical) iff: the action matched a mission must_ask_first boundary; OR the action was
    opaque (fail-closed); OR it fell into a capability class the envelope marks ``gated``; OR it
    CLAIMED a high-consequence class that the substrate manifest proves STRUCTURALLY ABSENT (a red
    flag). Otherwise OPEN -> falls through to the numeric gate. Fail-CLOSED throughout.

    ``manifest`` (a :class:`runner.substrate_manifest.SubstrateManifest`, or None) BINDS the class to
    the real substrate. It is duck-typed (needs ``.is_absent(cls)`` / ``.status(cls)``). When None
    (flag AQ_SUBSTRATE_BINDING off) the partition is byte-identical to the pre-substrate behaviour.
    ONE-WAY: the manifest can only turn an absent-but-claimed class into a HELD red flag; it never
    opens or downgrades a gate. UNKNOWN reachability is treated exactly like REACHABLE (fail-closed)."""
    c = classify_capability_classes(
        action, spends_money=spends_money, touches_human=touches_human, commits=commits,
        must_ask_first=must_ask_first)

    # Partition the high-consequence classes. With a manifest, a PROVEN-absent class the step claimed
    # is pulled OUT of the ordinary gated/open partition into a substrate red flag (still held). A
    # reachable/unknown class keeps its ordinary envelope disposition -- one-way: never lowered.
    substrate_mismatch: list[str] = []
    gated_classes_l: list[str] = []
    open_classes_l: list[str] = []
    for cls in c.classes:
        if manifest is not None and cls in HIGH_CONSEQUENCE_CLASSES and manifest.is_absent(cls):
            substrate_mismatch.append(cls)
        elif envelope.is_gated(cls):
            gated_classes_l.append(cls)
        elif cls in HIGH_CONSEQUENCE_CLASSES:
            open_classes_l.append(cls)
    gated_classes = tuple(gated_classes_l)
    open_classes = tuple(open_classes_l)
    substrate_mismatch_classes = tuple(substrate_mismatch)

    gated = bool(gated_classes) or bool(substrate_mismatch_classes) or c.must_ask_first_hit or c.fail_closed
    reasons = list(c.reasons)
    if gated_classes:
        reasons.append(f"envelope gates capability class(es): {', '.join(gated_classes)}")
    if open_classes:
        reasons.append(f"operator OPENED class(es) (fall through to numeric): {', '.join(open_classes)}")
    if substrate_mismatch_classes:
        reasons.append(
            "substrate mismatch (RED FLAG): step claims capability class(es) "
            f"{', '.join(substrate_mismatch_classes)} that the substrate structurally LACKS "
            "(no tool/grant present) -- the plan wants a capability it cannot have; holding for review")
    return EnvelopeVerdict(
        gated=gated, classes=c.classes, gated_classes=gated_classes, open_classes=open_classes,
        must_ask_first_hit=c.must_ask_first_hit, fail_closed=c.fail_closed,
        substrate_mismatch_classes=substrate_mismatch_classes, substrate_bound=manifest is not None,
        reasons=tuple(reasons))


def assess_plan_envelope(
    plan, envelope: AuthorityEnvelope, *, spends_money: bool = False, touches_human: bool = False,
    commits: bool = False, must_ask_first=None, manifest=None,
) -> EnvelopeVerdict:
    """Categorical envelope verdict over a whole plan: gated iff ANY step gates. Merges per-step
    verdicts, preferring a gated one so the reasons explain WHY the plan was held.

    ``manifest`` binds the class to the real substrate (see :func:`assess_action_envelope`); None
    (flag off) is byte-identical to the pre-substrate behaviour.

    A plan whose ``steps`` is empty/opaque fails CLOSED (gated) -- consistent with the numeric gate's
    no-steps -> level-3 behaviour."""
    steps = plan.get("steps") if isinstance(plan, Mapping) else None
    if not steps:
        return EnvelopeVerdict(
            gated=True, classes=(), gated_classes=(), open_classes=(), must_ask_first_hit=False,
            fail_closed=True, substrate_bound=manifest is not None,
            reasons=("plan has no steps -> fail-closed categorical gate",))
    verdicts = [
        assess_action_envelope(
            step.get("action") if isinstance(step, Mapping) else step, envelope,
            spends_money=spends_money, touches_human=touches_human, commits=commits,
            must_ask_first=must_ask_first, manifest=manifest)
        for step in steps
    ]
    # Prefer a gated verdict (that is the reason the plan is held); else return the first.
    worst = next((v for v in verdicts if v.gated), verdicts[0])
    all_classes = tuple(sorted({cls for v in verdicts for cls in v.classes}))
    gated_classes = tuple(sorted({cls for v in verdicts for cls in v.gated_classes}))
    open_classes = tuple(sorted({cls for v in verdicts for cls in v.open_classes}))
    substrate_mismatch = tuple(sorted({cls for v in verdicts for cls in v.substrate_mismatch_classes}))
    return EnvelopeVerdict(
        gated=any(v.gated for v in verdicts), classes=all_classes, gated_classes=gated_classes,
        open_classes=open_classes,
        must_ask_first_hit=any(v.must_ask_first_hit for v in verdicts),
        fail_closed=any(v.fail_closed for v in verdicts),
        substrate_mismatch_classes=substrate_mismatch, substrate_bound=manifest is not None,
        reasons=worst.reasons)
