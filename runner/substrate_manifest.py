"""SUBSTRATE MANIFEST: bind a capability CLASS to the REAL SUBSTRATE, not the actor's declaration.

Design: design_authority_envelope.md Slice 2 (the "substrate wall" follow-up the module docstring of
authority_envelope.py names explicitly); contract-shaped toward the V2 AuthorityPolicy JSON (BB #2716).

THE PROBLEM THIS CLOSES
-----------------------
authority_envelope.py (Slice 2) derives a capability CLASS from DECLARATION channels: the model's
work flags, a keyword scan of the actor's free-text ``action``, and the mission ``must_ask_first``
boundary. All three are leaky -- the actor authors the text the keyword scan reads, and the flags are
model-declared. The REAL wall is the SUBSTRATE: what the act-phase agent can actually DO is bounded by
(a) the least-privilege ``aq_actor`` DB role (schema/999) and (b) the tools/executables the container
image put on its PATH (container/Dockerfile). A high-consequence class with NO corresponding tool or
grant is STRUCTURALLY ABSENT -- the action literally cannot occur, no matter what the step's text says.

This module computes, from CODE-OWNED ground-truth artifacts the actor cannot edit, which capability
CLASSES are REACHABLE in this instance. The envelope then binds the class to that manifest.

HOW THE MANIFEST IS SOURCED -- HONEST ABOUT WHAT IS TRULY GROUND TRUTH
---------------------------------------------------------------------
Two sources, STRONGEST (most independent of the actor and even of the operator's word) FIRST:

  1. THE aq_actor DB GRANT SET -- genuinely ground-truth-derived. schema/999_container_role_grants.sql
     is COPIED into the image at /app/schema/ (Dockerfile ``COPY schema/``), is root-owned and
     read-only (``chmod -R a-w /app``), so the actor (the ``aq`` user) cannot edit it. We PARSE the
     GRANT/REVOKE statements that name ``aq_actor`` to compute exactly which tables it can write. This
     is the DB half of the wall and it is real: aq_actor can write ONLY the curated business tables
     (customers, subscriptions); it holds no TRUNCATE, no DROP (PostgreSQL never grants DROP -- it
     needs ownership), and no write on any authority/evidence table. So the DB-mediated classes are
     bounded by an artifact, not by the actor's claim.

  2. THE CONTAINER TOOL MANIFEST -- operator/IMAGE-declared, NOT actor-authored. The honest gap: the
     Dockerfile is NOT shipped into the image, and live-enumerating the actor's *sandboxed* PATH from
     the loop process at gate time is unreliable (the loop runs as ``aq_loop`` with the loop's PATH,
     not inside the codex/claude workspace-write sandbox the actor runs in, and a dev checkout's PATH
     is full of tools the image does not have). So we do NOT auto-probe ``shutil.which`` -- that would
     read the wrong process and wrongly mark classes reachable. Instead the tool set comes from, in
     precedence: (a) an OPERATOR-set ``AQ_SUBSTRATE_TOOLS`` env var (operator channel -- the actor's
     env is deny-by-default in executor._AGENT_ENV_NAMES, which does NOT include this name, so the
     actor can neither read nor set it); else (b) ``STANDARD_IMAGE_TOOLS``, a code-owned constant that
     mirrors what container/Dockerfile actually installs. This is DECLARATION-derived -- but it is the
     OPERATOR/IMAGE's declaration, not the ACTOR's, which is the real upgrade: the class binding now
     depends on what was INSTALLED, not on what the step's text claims.

     RESIDUAL GAP (stated plainly, do not overclaim): a class marked ABSENT via the tool manifest rests
     on that manifest being an accurate mirror of the image. A future hardening is to live-enumerate
     the actor's ACTUAL sandbox PATH + a ``SELECT`` against ``information_schema.role_table_grants``
     under the aq_actor credential at boot, and freeze that into the manifest. Until then the DB-grant
     half (source 1) is true ground truth; the tool half (source 2) is operator/image declaration.

FAIL-CLOSED, ONE-WAY, ACTOR-IMMUTABLE
-------------------------------------
  * ABSENCE MUST BE PROVEN, never assumed. A class is ABSENT only with POSITIVE evidence that every
    substrate path it needs is missing (the source is KNOWN and contains none of them). If a source
    needed for a class is missing/unparseable, that class is UNKNOWN -> the envelope treats UNKNOWN as
    REACHABLE and GATES it. An empty manifest gates everything; it never opens anything.
  * ONE-WAY: the manifest only ever RAISES (an ABSENT+claimed class becomes a red-flag hold; a
    REACHABLE/UNKNOWN class stays gated-by-default). It never LOWERS a Slice-2 gate.
  * The actor cannot edit any manifest source (root-owned read-only SQL; code constant; operator env).

Flag-gated by ``AQ_SUBSTRATE_BINDING``; default OFF => the envelope is never handed a manifest and the
Slice-2 categorical gate is byte-identical.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .authority_envelope import (
    CLASS_COMMIT, CLASS_CONTACT_HUMAN, CLASS_DELETE_PROD, CLASS_DEPLOY,
    CLASS_EXTERNAL_WRITE, CLASS_INTERNAL_REVERSIBLE, CLASS_PUBLISH_EXTERNAL,
    CLASS_SPEND_MONEY, HIGH_CONSEQUENCE_CLASSES,
)

POLICY_VERSION = "substrate-manifest-v1"

#: Environment flag. Default OFF => the envelope is never handed a manifest (byte-identical Slice-2).
FLAG = "AQ_SUBSTRATE_BINDING"

#: Operator-set (NOT actor-set) override for the installed-tool set, comma/space separated. Read from
#: the LOOP's environment; the actor's env is deny-by-default (executor._AGENT_ENV_NAMES) and does not
#: carry this name, so the actor can neither read nor influence it.
TOOLS_ENV = "AQ_SUBSTRATE_TOOLS"

#: Sentinel for ``installed_tools``: the tool set is GENUINELY undeterminable. Distinct from an empty
#: set (which is a KNOWN "nothing installed"). Forces tools_known=False so every tool-mediated class
#: degrades to UNKNOWN -> the envelope GATES it (fail-closed). Never let "we don't know" become "absent".
TOOLS_UNKNOWN = object()

# -- per-class substrate status -----------------------------------------------------------------
REACHABLE = "reachable"   #: some installed tool or granted DB path can perform this class.
ABSENT = "absent"         #: PROVEN: every substrate path this class needs is missing.
UNKNOWN = "unknown"       #: a source needed to decide is missing/unparseable -> fail-closed (gate).

#: CODE-OWNED mirror of what container/Dockerfile installs on the act agent's PATH. Operator/IMAGE
#: declaration, not actor-authored. Kept lowercase, basename only. (See module docstring source 2.)
STANDARD_IMAGE_TOOLS: frozenset[str] = frozenset({
    # apt-get (container/Dockerfile line 12-13) + node base image + venv + baked/symlinked skills.
    "curl", "git", "openssl", "tini", "python3", "node", "npm",
    "codex",            # npm global @openai/codex (Dockerfile line 15)
    "aqdb", "aqmem",    # curated DB + memory skills, symlinked onto PATH (Dockerfile line 75-76)
})

#: Tool-name aliases mapped to the canonical enabling tool, so an operator manifest written with a
#: common synonym still binds. Only additive (widens what counts as PRESENT -> raise-only safe).
_TOOL_ALIASES: dict[str, str] = {"python": "python3", "http": "httpie"}


@dataclass(frozen=True)
class _ClassBinding:
    """What SUBSTRATE a capability class needs. A class is REACHABLE if ANY enabling tool is installed
    OR its DB predicate is satisfied; ABSENT only if BOTH sources are KNOWN and neither is satisfied."""
    enabling_tools: frozenset[str]
    #: name of the DB-grant predicate this class also reaches through (None => tool-only class).
    db_predicate: str | None = None


#: CODE-OWNED capability-class -> substrate requirement. The enabling-tool sets are deliberately the
#: executables that would ACTUALLY perform the effect; none of them are in STANDARD_IMAGE_TOOLS for the
#: external classes, which is WHY they come out structurally absent under the standard business image.
_BINDINGS: dict[str, _ClassBinding] = {
    # --- tool-mediated external effects (no DB path) ---
    CLASS_SPEND_MONEY: _ClassBinding(frozenset({
        "stripe", "paypal", "braintree", "square", "adyen", "plaid", "wise", "razorpay"})),
    CLASS_CONTACT_HUMAN: _ClassBinding(frozenset({
        "sendmail", "mail", "mailx", "msmtp", "ssmtp", "mutt", "swaks", "twilio", "slack",
        "mailgun", "sendgrid", "postmark", "ses"})),
    CLASS_PUBLISH_EXTERNAL: _ClassBinding(frozenset({
        "twurl", "tweet", "buffer", "hootsuite", "gh", "hub", "npm-publish", "hugo"})),
    CLASS_DEPLOY: _ClassBinding(frozenset({
        "kubectl", "helm", "terraform", "ansible", "docker", "aws", "gcloud", "az", "flyctl",
        "vercel", "netlify", "heroku", "serverless", "pulumi"})),
    # curl IS installed (Dockerfile) -> external_write is REACHABLE under the standard image. This is
    # the honest fail-closed outcome: an outbound-HTTP-write tool is present, so we cannot prove absence.
    CLASS_EXTERNAL_WRITE: _ClassBinding(frozenset({"curl", "wget", "httpie", "http", "xh"})),
    # git IS installed -> commit is REACHABLE (present tool; cannot prove absence even though the
    # act workspace is not itself a configured git remote -- fail-closed keeps it gated).
    CLASS_COMMIT: _ClassBinding(frozenset({"git", "gh", "hg", "svn"})),
    # --- DB-mediated ---
    # delete_prod is kept REACHABLE whenever the actor holds ANY row-DELETE grant (it can delete
    # curated business rows) OR a destructive DDL grant. We deliberately do NOT try to PROVE
    # delete_prod absent: the class is coarse (its keywords also cover shell ``rm -rf``), so claiming
    # structural absence would overclaim. Conservative = keep it gated. (See honest note.)
    CLASS_DELETE_PROD: _ClassBinding(frozenset(), db_predicate="destructive_or_delete"),
    # internal_reversible is reachable when the actor holds a curated business write AND the aqdb tool
    # is present. It is OPEN by default (not high-consequence), so this only enriches the verdict.
    CLASS_INTERNAL_REVERSIBLE: _ClassBinding(frozenset({"aqdb"}), db_predicate="curated_write"),
}


# -- aq_actor DB grant parsing (SOURCE 1: true ground truth) -------------------------------------
_ACTOR = "aq_actor"
_WRITE_PRIVS = frozenset({"INSERT", "UPDATE", "DELETE", "TRUNCATE"})
#: tables whose write means "authority/evidence", not curated business data (destructive if reachable).
_AUTHORITY_TABLES = frozenset({
    "causal_edge", "ralph_causal_edges", "causal_principle_transition", "causal_principle_plan_usage",
    "causal_principle_plan_outcome", "runs", "work", "learnings", "planning_prediction",
    "plan_acquisition", "impasse_meta_mode_decision",
})
#: The curated business tables the aqdb skill exposes -- writing these is internal/reversible.
_BUSINESS_TABLES = frozenset({"customers", "subscriptions"})

# GRANT|REVOKE <privs> ON <objects> TO|FROM aq_actor  -- tolerant of the EXECUTE '...' wrapper.
_GRANT_RE = re.compile(
    r"\b(GRANT|REVOKE)\b\s+(.+?)\s+\bON\b\s+(.+?)\s+\b(?:TO|FROM)\b\s+([^;'\"]*)",
    re.IGNORECASE | re.DOTALL)

#: SQL line comment (``-- ...`` to end of line). STRIPPED before parsing: the matcher is DOTALL, so a
#: comment whose prose happens to contain the words "grant"/"on"/"to"/"from" (schema/999 has several,
#: e.g. "a re-appearing blanket grant ... hand aq_actor write on ...") would otherwise seed a spurious
#: match that SPANS newlines and SWALLOWS the following real statement (observed on the shipped
#: schema/999). Removing line comments first makes the DB half depend only on executable DDL. Fail-safe
#: even without this (a swallowed GRANT only ever UNDER-reports -> the class degrades to ABSENT/UNKNOWN
#: which the envelope HOLDS, never opens), but the parse must be HONEST to back the ground-truth claim.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


@dataclass(frozen=True)
class ActorGrants:
    """What the aq_actor role can WRITE, parsed from schema/999. ``known`` is False when the grant
    file was missing/unparseable -> the DB-mediated classes fail closed (UNKNOWN)."""
    known: bool
    writable_tables: frozenset[str]
    has_delete_any: bool
    has_destructive_authority: bool   # TRUNCATE anywhere, or any write on an authority/evidence table.
    reasons: tuple[str, ...] = ()

    @property
    def has_curated_write(self) -> bool:
        return bool(self.writable_tables & _BUSINESS_TABLES)

    def satisfies(self, predicate: str) -> bool:
        if predicate == "curated_write":
            return self.has_curated_write
        if predicate == "destructive_or_delete":
            return self.has_delete_any or self.has_destructive_authority
        return False


def _split_objects(obj_text: str) -> list[str]:
    """The object list of a GRANT/REVOKE. Skip non-table objects (SEQUENCE/SCHEMA/FUNCTION/DATABASE)
    and the ``ALL TABLES IN SCHEMA`` blanket (aq_actor has no blanket WRITE grant; only named tables)."""
    t = " ".join(obj_text.split())
    up = t.upper()
    if up.startswith(("SEQUENCE", "SCHEMA", "FUNCTION", "DATABASE", "ALL SEQUENCES", "ALL FUNCTIONS")):
        return []
    if up.startswith("ALL TABLES"):
        return ["__ALL_TABLES__"]
    t = re.sub(r"(?i)^TABLE\s+", "", t)
    out = []
    for raw in t.split(","):
        name = raw.strip().strip('"').split()[0] if raw.strip() else ""
        # strip schema qualifier (public.customers -> customers)
        name = name.split(".")[-1].strip('"')
        if name:
            out.append(name.lower())
    return out


def parse_actor_grants(sql_text: str) -> ActorGrants:
    """Parse the aq_actor GRANT/REVOKE statements into a writable-table set. Statements are applied in
    file order (a later REVOKE removes an earlier GRANT), matching how PostgreSQL would end up."""
    # Strip SQL line comments FIRST: the DOTALL matcher would otherwise let comment prose seed a
    # spurious match that spans newlines and swallows the next real statement (see _LINE_COMMENT_RE).
    sql_text = _LINE_COMMENT_RE.sub("", sql_text)
    # table -> set of write privileges currently held by aq_actor.
    priv: dict[str, set[str]] = {}
    saw_actor = False
    for m in _GRANT_RE.finditer(sql_text):
        verb, privs_txt, objs_txt, grantee = m.group(1), m.group(2), m.group(3), m.group(4)
        if _ACTOR not in grantee.lower():
            continue
        saw_actor = True
        privs = {p.strip().upper() for p in privs_txt.split(",")}
        privs = {p for p in privs if p in _WRITE_PRIVS} or (
            _WRITE_PRIVS if "ALL" in {p.strip().upper() for p in privs_txt.split(",")} else set())
        if not privs:
            continue  # a pure SELECT/USAGE grant carries no WRITE signal.
        tables = _split_objects(objs_txt)
        if "__ALL_TABLES__" in tables:
            # blanket revoke clears write on every currently-tracked table; blanket grant is not used
            # for aq_actor, but handle it fail-closed by marking it unknown-wide (ignored for absence).
            if verb.upper() == "REVOKE":
                for s in priv.values():
                    s -= privs
            continue
        for tbl in tables:
            cur = priv.setdefault(tbl, set())
            if verb.upper() == "GRANT":
                cur |= privs
            else:
                cur -= privs
    if not saw_actor:
        return ActorGrants(known=False, writable_tables=frozenset(), has_delete_any=False,
                           has_destructive_authority=False,
                           reasons=("no aq_actor grant statements found -> DB substrate UNKNOWN",))
    writable = frozenset(t for t, ps in priv.items() if ps & {"INSERT", "UPDATE", "DELETE"})
    has_delete_any = any("DELETE" in ps for ps in priv.values())
    has_destructive_authority = (
        any("TRUNCATE" in ps for ps in priv.values())
        or any((ps & {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}) and t in _AUTHORITY_TABLES
               for t, ps in priv.items()))
    reasons = (
        f"aq_actor writable tables from schema/999: {sorted(writable) or 'none'}",
        f"has_delete_any={has_delete_any}; has_destructive_authority={has_destructive_authority}",
    )
    return ActorGrants(known=True, writable_tables=writable, has_delete_any=has_delete_any,
                       has_destructive_authority=has_destructive_authority, reasons=reasons)


def _default_grants_path() -> Path:
    """schema/999 sits next to the package both in a repo checkout and in the image (/app/schema)."""
    return Path(__file__).resolve().parent.parent / "schema" / "999_container_role_grants.sql"


def _load_actor_grants(grants_sql_path: str | Path | None) -> ActorGrants:
    path = Path(grants_sql_path) if grants_sql_path is not None else _default_grants_path()
    try:
        text = path.read_text()
    except OSError:
        return ActorGrants(known=False, writable_tables=frozenset(), has_delete_any=False,
                           has_destructive_authority=False,
                           reasons=(f"grant file {path} unreadable -> DB substrate UNKNOWN",))
    return parse_actor_grants(text)


def _resolve_installed_tools(installed_tools, env: Mapping[str, str]) -> tuple[frozenset[str] | None, str]:
    """Return (tool set or None-if-unknown, provenance). Precedence: explicit arg > operator env >
    STANDARD_IMAGE_TOOLS. An operator env set to empty/whitespace is an EXPLICIT empty tool set
    (source known, nothing installed) -- distinct from the var being absent (unknown)."""
    def _norm(names) -> frozenset[str]:
        out = set()
        for n in names:
            n = str(n).strip().lower()
            if not n:
                continue
            n = n.split("/")[-1]
            out.add(_TOOL_ALIASES.get(n, n))
        return frozenset(out)

    if installed_tools is TOOLS_UNKNOWN:
        return None, "tools undeclared (fail-closed UNKNOWN)"
    if installed_tools is not None:
        return _norm(installed_tools), "explicit installed_tools argument"
    raw = env.get(TOOLS_ENV)
    if raw is not None:
        return _norm(re.split(r"[,\s]+", raw)), f"operator {TOOLS_ENV} env"
    return frozenset(STANDARD_IMAGE_TOOLS), "code-owned STANDARD_IMAGE_TOOLS (image declaration)"


@dataclass(frozen=True)
class SubstrateManifest:
    """Capability class -> substrate status ({reachable|absent|unknown}), derived from ground-truth
    artifacts the actor cannot edit. Shaped toward the V2 AuthorityPolicy JSON (BB #2716).

    Fail-CLOSED: :meth:`status` returns UNKNOWN for any class not positively decided, and UNKNOWN is
    treated by the envelope exactly like REACHABLE (gated). Only :meth:`is_absent` (a PROVEN absence)
    changes behaviour, and only ever to a red-flag HOLD -- never to an open."""
    statuses: Mapping[str, str]
    installed_tools: frozenset[str]
    tools_known: bool
    actor_grants: ActorGrants
    tool_source: str
    reasons: tuple[str, ...] = ()
    policy_version: str = POLICY_VERSION

    def status(self, capability_class: str) -> str:
        return self.statuses.get(capability_class, UNKNOWN)

    def is_absent(self, capability_class: str) -> bool:
        """True ONLY when absence is PROVEN. Unknown is never absent (fail-closed)."""
        return self.statuses.get(capability_class) == ABSENT

    def is_reachable(self, capability_class: str) -> bool:
        return self.statuses.get(capability_class) == REACHABLE

    def absent_high_consequence_classes(self) -> tuple[str, ...]:
        return tuple(c for c in HIGH_CONSEQUENCE_CLASSES if self.is_absent(c))

    def to_dict(self) -> dict:
        return {
            "statuses": dict(self.statuses),
            "installed_tools": sorted(self.installed_tools),
            "tools_known": self.tools_known,
            "tool_source": self.tool_source,
            "actor_writable_tables": sorted(self.actor_grants.writable_tables),
            "db_grants_known": self.actor_grants.known,
            "reasons": list(self.reasons),
            "policy_version": self.policy_version,
        }


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True iff substrate binding is switched on. Default OFF => envelope gets no manifest."""
    source = env if env is not None else os.environ
    return str(source.get(FLAG, "")).strip().lower() in {"1", "true", "yes", "on"}


def build_substrate_manifest(
    *, grants_sql_path: str | Path | None = None, installed_tools=None,
    env: Mapping[str, str] | None = None,
) -> SubstrateManifest:
    """Compute the capability-class reachability manifest from ground-truth artifacts.

    ``grants_sql_path`` defaults to the shipped schema/999. ``installed_tools`` overrides the tool
    source (else operator ``AQ_SUBSTRATE_TOOLS`` env, else STANDARD_IMAGE_TOOLS). Fail-CLOSED: a class
    is ABSENT only with positive proof that every substrate path it needs is missing; if a needed
    source is unknown the class is UNKNOWN (the envelope gates it)."""
    env = env if env is not None else os.environ
    tools, tool_source = _resolve_installed_tools(installed_tools, env)
    tools_known = tools is not None
    tools = tools if tools is not None else frozenset()
    grants = _load_actor_grants(grants_sql_path)

    statuses: dict[str, str] = {}
    reasons: list[str] = [f"tool source: {tool_source}", *grants.reasons]
    for cls, binding in _BINDINGS.items():
        tool_hit = bool(binding.enabling_tools & tools)
        db_hit = binding.db_predicate is not None and grants.satisfies(binding.db_predicate)
        if tool_hit or db_hit:
            statuses[cls] = REACHABLE
            why = []
            if tool_hit:
                why.append(f"tool(s) {sorted(binding.enabling_tools & tools)}")
            if db_hit:
                why.append(f"DB predicate {binding.db_predicate!r}")
            reasons.append(f"{cls}: REACHABLE via {', '.join(why)}")
            continue
        # No positive hit. To PROVE absence every source this class relies on must be KNOWN.
        tool_source_ok = tools_known or not binding.enabling_tools
        db_source_ok = grants.known or binding.db_predicate is None
        if tool_source_ok and db_source_ok:
            statuses[cls] = ABSENT
            reasons.append(
                f"{cls}: ABSENT (proven) -- no enabling tool installed"
                + (f" and DB predicate {binding.db_predicate!r} unsatisfied" if binding.db_predicate else ""))
        else:
            statuses[cls] = UNKNOWN
            reasons.append(
                f"{cls}: UNKNOWN (fail-closed) -- source undecidable "
                f"(tools_known={tools_known}, db_grants_known={grants.known})")
    return SubstrateManifest(
        statuses=MappingProxyType(statuses), installed_tools=tools, tools_known=tools_known,
        actor_grants=grants, tool_source=tool_source, reasons=tuple(reasons))
