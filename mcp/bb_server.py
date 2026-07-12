#!/usr/bin/env python3
"""The blackboard, exposed as an MCP server.

Without this, the instance's memory is PRIVATE to its own loop: only `db.live_learnings()` reads
it. Which means a second agent — a reviewer, a specialist, a parallel worker, or just you asking
a question in your terminal — cannot see a thing the system has learned, and will cheerfully
repeat mistakes it already paid for.

This makes the memory ASKABLE. Any MCP-speaking agent (Codex, Claude Code, Copilot) can now:

    what have we learned about X?          -> bb_search
    what's the state of this instance?     -> bb_context
    we settled this; stop re-arguing it    -> bb_decide
    your turn, here's where I got to       -> bb_handoff / bb_inbox

Stdio JSON-RPC, no dependencies beyond psycopg2. Wire it into an agent with:

    codex mcp add autonomy-quest -- python3 /path/to/mcp/bb_server.py
    claude mcp add autonomy-quest -- python3 /path/to/mcp/bb_server.py
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal

import psycopg2
import psycopg2.extras

AGENT = os.environ.get("AQ_AGENT_NAME", "agent")


def q(sql, args=(), one=False):
    with psycopg2.connect(os.environ["AQ_DB_URL"]) as c, \
         c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, args)
        if cur.description is None:
            return None
        return cur.fetchone() if one else cur.fetchall()


def _safe(o):
    return float(o) if isinstance(o, Decimal) else str(o)


def dump(rows) -> str:
    return json.dumps(rows, default=_safe, indent=2)


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------

def _lexemes(query: str) -> list[str]:
    """Words -> tsquery lexemes, OR-able. Drops punctuation that would make to_tsquery throw."""
    import re
    return [w for w in re.findall(r"[A-Za-z0-9_]+", query.lower()) if len(w) > 2]


def bb_context(**_):
    """Everything an agent needs to not start from zero.

    The mission's number is RE-READ FROM ITS REAL SOURCE, every call. It is not read from the
    measurements table.

    That distinction is not pedantry — it cost us a false audit. measurements is written during
    observe(), at the START of a cycle, so the newest row is a SNAPSHOT FROM BEFORE the cycle
    acted. A reviewer agent read it (40), compared it against the 60 rows actually in the table,
    "found" a 60-vs-40 discrepancy, and filed a critical finding about work that was in fact
    correct. The board handed a stale proxy to an agent, and the agent reasoned faithfully to a
    false conclusion.

    A cached number is not the number. loop.observe() re-reads it; the UI re-reads it; so does
    this. Anything that reports the mission's state must gate on ground truth, or it will
    manufacture bugs that do not exist.
    """
    import yaml
    measure = ((yaml.safe_load(open("instance.yaml")) or {}).get("mission") or {}).get("measure") or {}
    try:
        row = q(measure["where"])[0]
        live = {"metric": measure.get("what"), "value": float(list(row.values())[0]),
                "source": "re-read live from the mission's own query"}
    except Exception as e:
        # Say so. Do NOT fall back to the cached measurement — a stale number presented as
        # current is precisely the failure above.
        live = {"metric": measure.get("what"), "value": None,
                "error": f"could not read the measure: {e}",
                "source": "UNREADABLE — do not substitute a remembered value"}

    return dump({
        "mission_now": live,
        "measurement_history_note":
            "measurements[] are SNAPSHOTS taken at the start of each cycle. They are history, "
            "not current state. Never compare them against live rows and conclude a discrepancy.",
        "loop": q("""select count(*) as cycles, max(completed_at) as last_cycle
                     from runs r join learnings l on l.run_id=r.id
                     where r.completed_at is not null""", one=True),
        # What it BELIEVES. Superseded beliefs excluded — this is current, not historical.
        "learnings": q("""select insight, evidence, scope, confidence from learnings
                          where superseded_by is null
                          order by confidence desc, created_at desc limit 25"""),
        "decisions": q("""select id, title, body, author, created_at from bb_notes
                          where kind='decision' and id not in
                            (select supersedes from bb_notes where supersedes is not null)
                          order by created_at desc limit 15"""),
        "parked_for_human": q("select id, summary, rationale from work where status='awaiting_human'"),
        "recent_failures": q("""select r.id, w.summary, r.outcome from runs r join work w on w.id=r.work_id
                                where r.succeeded is false order by r.completed_at desc limit 5"""),
    })


def bb_search(query: str, limit: int = 10, **_):
    """What do we know about X? Searches learnings AND the board together — an agent asking a
    question does not care which table the answer happens to live in.

    OR semantics, deliberately. plainto_tsquery ANDs every term, so asking
    "write capability database" found NOTHING even though the instance had learned exactly that
    — one absent word ("database") zeroed the whole query. A blackboard that confidently reports
    "nothing known" about something it DOES know is worse than no blackboard: the agent then
    repeats the very mistake the search existed to prevent. Rank sorts the partial matches; a
    weak hit is infinitely better than a false "we never tried this".
    """
    tsq = " | ".join(w for w in _lexemes(query)) or "''"
    learn = q("""select 'learning' as src, insight as title, evidence as body,
                        scope, confidence, created_at,
                        ts_rank(to_tsvector('english', insight || ' ' || evidence),
                                to_tsquery('english', %s)) as rank
                 from learnings
                 where superseded_by is null
                   and to_tsvector('english', insight || ' ' || evidence)
                       @@ to_tsquery('english', %s)
                 order by rank desc limit %s""", (tsq, tsq, limit))
    notes = q("""select kind as src, title, body, null as scope, null as confidence, created_at,
                        ts_rank(to_tsvector('english', title || ' ' || body),
                                to_tsquery('english', %s)) as rank
                 from bb_notes
                 where to_tsvector('english', title || ' ' || body)
                       @@ to_tsquery('english', %s)
                 order by rank desc limit %s""", (tsq, tsq, limit))
    hits = sorted(learn + notes, key=lambda r: r["rank"], reverse=True)[:limit]
    if not hits:
        return f"Nothing on the board about {query!r}. That is a real answer — do not infer that " \
               f"it was tried and failed. It simply has not been recorded."
    return dump(hits)


def bb_note(title: str, body: str, tags: list | None = None, **_):
    r = q("insert into bb_notes (author, kind, title, body, tags) values (%s,'note',%s,%s,%s) returning id",
          (AGENT, title, body, tags or []), one=True)
    return f"noted #{r['id']}"


def bb_decide(title: str, body: str, supersedes: int | None = None, **_):
    """Record a DECISION so it stops being re-argued every session.

    This is most of the blackboard's value, and it is not the note — it's the not-relitigating.
    """
    r = q("""insert into bb_notes (author, kind, title, body, supersedes)
             values (%s,'decision',%s,%s,%s) returning id""",
          (AGENT, title, body, supersedes), one=True)
    return f"decision #{r['id']} recorded. It should not be re-argued without superseding it."


def bb_handoff(to: str, subject: str, body: str, **_):
    """Hand work to another agent. Durable — it survives your process dying, which is exactly
    when a handoff matters most."""
    r = q("""insert into bb_messages (sender, recipient, subject, body)
             values (%s,%s,%s,%s) returning id""", (AGENT, to, subject, body), one=True)
    return f"handed off to {to} as message #{r['id']} (durable — it survives your process dying)"


def bb_inbox(mark_delivered: bool = True, **_):
    """Messages for me. Reading them is what marks them delivered — 'sent' has never meant
    'delivered', and a row is the only thing that proves anyone picked it up."""
    rows = q("""select id, sender, subject, body, created_at from bb_messages
                where (recipient = %s or recipient = '*') and delivered_at is null
                order by created_at""", (AGENT,))
    if rows and mark_delivered:
        q("update bb_messages set delivered_at = now() where id = any(%s)",
          ([r["id"] for r in rows],))
    return dump(rows) if rows else "empty inbox"


def bb_learnings(scope: str | None = None, **_):
    """What this instance currently believes. `scope=generalisable` gives the ones that would
    hold for a DIFFERENT instance with a different mission — the ones worth sharing."""
    if scope:
        return dump(q("""select insight, evidence, scope, confidence from learnings
                         where superseded_by is null and scope=%s
                         order by confidence desc limit 50""", (scope,)))
    return dump(q("""select insight, evidence, scope, confidence from learnings
                     where superseded_by is null order by confidence desc limit 50"""))


TOOLS = {
    "bb_context":   (bb_context,   "Everything about this instance: mission, loop state, what it believes, what's parked, recent failures. Call this FIRST — it is how you avoid starting from zero.", {}),
    "bb_search":    (bb_search,    "What do we know about X? Searches learnings and the board together.",
                     {"query": {"type": "string"}, "limit": {"type": "integer"}}),
    "bb_learnings": (bb_learnings, "What this instance currently believes. scope=generalisable for the ones that would transfer to another instance.",
                     {"scope": {"type": "string", "enum": ["local", "generalisable"]}}),
    "bb_note":      (bb_note,      "Write something to the shared board so other agents can see it.",
                     {"title": {"type": "string"}, "body": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}),
    "bb_decide":    (bb_decide,    "Record a DECISION so it is not re-litigated next session. Include WHY.",
                     {"title": {"type": "string"}, "body": {"type": "string"}, "supersedes": {"type": "integer"}}),
    "bb_handoff":   (bb_handoff,   "Hand work to another agent. Durable: survives your process dying.",
                     {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}),
    "bb_inbox":     (bb_inbox,     "Messages addressed to you. Reading marks them delivered.", {}),
}


# ---------------------------------------------------------------------------
# MCP stdio plumbing
# ---------------------------------------------------------------------------

def reply(rid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, rid, params = req.get("method"), req.get("id"), req.get("params") or {}

        if method == "initialize":
            reply(rid, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "autonomy-quest-bb", "version": "1.0.0"},
            })
        elif method == "tools/list":
            reply(rid, {"tools": [
                {"name": n, "description": d,
                 "inputSchema": {"type": "object", "properties": p,
                                 "required": [k for k in p if k in ("query", "title", "body", "to", "subject")]}}
                for n, (_, d, p) in TOOLS.items()
            ]})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            fn = TOOLS.get(name, (None,))[0]
            if fn is None:
                reply(rid, {"content": [{"type": "text", "text": f"no such tool: {name}"}], "isError": True})
                continue
            try:
                out = fn(**args)
                reply(rid, {"content": [{"type": "text", "text": str(out)}]})
            except Exception as e:
                # Fail loud. A blackboard that silently swallows an error is a blackboard that
                # lies about what it knows.
                reply(rid, {"content": [{"type": "text", "text": f"{name} failed: {e}"}], "isError": True})
        elif rid is not None:
            reply(rid, {})


if __name__ == "__main__":
    main()
