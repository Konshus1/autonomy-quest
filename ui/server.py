"""The window into a running instance.

Deliberately stdlib-only — no FastAPI, no node, no build step. This whole system is supposed to
be handable to someone who has never seen it, and every dependency is a way for that to fail on
their machine.

What it shows, in priority order:

  1. IS THE LOOP TURNING?  Not "is the process up" — when did a cycle last complete a full
     acted→recorded→LEARNED pass? A stalled loop behind a healthy-looking process is the failure
     this whole system exists to make impossible, so it is the first thing on the page.
  2. Is the mission's number moving?  And how close to the target?
  3. What is it waiting on YOU for? A parked decision nobody looks at is a dead instance.
  4. What has it learned? This is the part that makes it more than a cron job.

The health banner is derived SERVER-SIDE (guardrail 7: the page renders status verbatim, never
decides it for itself). Seven states, first match wins — and AT_TARGET is proven by a fresh
MEASUREMENT for the mission's metric (or a fresh completed cycle), never by a heartbeat. A
heartbeat proves the process is alive; it does not prove the mission is still at its target. Only
a fresh measurement does that. At target with a stale measurement is STALLED, honestly, because
we no longer know the number is still there.

Every string that crosses from the model or the database onto the page goes through esc() before
innerHTML. The page carries a visible XSS canary — a <script>+<img onerror> payload rendered
through the same esc() every DB field uses — so the proof that it renders inert is on the page
itself, not in a claim.
"""

from __future__ import annotations

import json
import os
from hmac import compare_digest
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg2
import psycopg2.extras
import yaml

from runner.approval import ApprovalInvalid, assert_valid_approval

STALL_MINUTES = int(os.environ.get("AQ_STALL_MINUTES", "180"))

# A constant payload that proves esc() makes model/DB text inert when inserted via innerHTML.
# Rendered visibly on the page through the SAME esc() path every DB field takes. If you can see
# it as text, the escaping held; the JS assertion below confirms nothing executed.
_XSS_CANARY = (
    "<script>window.__xss_FIRED='script'</script>"
    "<img src=x onerror=window.__xss_FIRED='img'>"
)

# Last-good cache. On a DB failure the page degrades to CANT_SEE (grey) and DIMS the prior
# payload — it never keeps showing green on data it can no longer verify (guardrail 2).
_LAST_GOOD: dict | None = None
_LAST_GOOD_AT: datetime | None = None


def _conn():
    return psycopg2.connect(os.environ["AQ_DB_URL"])


def _rows(sql, args=()):
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else []


def _json_safe(o):
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def _approval_token_from_headers(headers) -> str:
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip()
    return headers.get("X-AQ-Approval-Token", "").strip()


def _approval_authorized(headers) -> tuple[bool, str]:
    expected = os.environ.get("AQ_APPROVAL_TOKEN", "").strip()
    if not expected:
        return False, "approval token is not configured"
    supplied = _approval_token_from_headers(headers)
    if not supplied:
        return False, "approval token is required"
    if not compare_digest(supplied, expected):
        return False, "approval token was rejected"
    return True, ""


def _read_instance():
    """instance.yaml -> (inst, mission, measure). Never raises: a missing mission is a state
    (NOT_ALIVE), not an exception. The file is always readable even when the DB is not."""
    inst = yaml.safe_load(open("instance.yaml")) or {}
    mission = inst.get("mission") or {}
    measure = mission.get("measure") or {}
    return inst, mission, measure


def _resolve_target(measure):
    """Live target resolution, matching loop.py observe(). target_query wins (re-read from ground
    truth every call); a frozen target is the fallback. Per-query catch: a bad target_query is a
    measure error, not a DB-down."""
    if measure.get("target_query"):
        try:
            row = _rows(measure["target_query"])[0]
            return float(list(row.values())[0]), None
        except Exception as e:
            return None, str(e).strip().split("\n")[0]
    if measure.get("target") is not None:
        try:
            return float(measure["target"]), None
        except Exception as e:
            return None, str(e).strip().split("\n")[0]
    return None, None


def _db_state(measure, mission):
    """All DB-derived data. Raises on a connection-level failure (caught by state() -> CANT_SEE).
    Per-query failures on the measure/target queries are caught here (bad query ≠ DB down)."""
    # THE number, re-read from its real source. Not cached, not remembered.
    try:
        now_val = float(list(_rows(measure["where"])[0].values())[0])
        measure_error = None
    except Exception as e:
        now_val, measure_error = None, str(e).strip().split("\n")[0]

    target, target_error = _resolve_target(measure)
    goal = measure.get("goal", "reach_and_maintain")
    satisfied = (goal == "reach_and_maintain" and target is not None
                 and now_val is not None and now_val >= target)
    overshooting = (goal == "reach_and_maintain" and target is not None
                    and now_val is not None and now_val > target * 1.5)

    metric = measure.get("what") or ""

    # IS THE LOOP TURNING? Ground truth: a completed run WITH a learning, recently.
    turning = _rows(
        """select count(*) as n, max(r.completed_at) as last,
                  extract(epoch from now() - max(r.completed_at))/60 as last_age_min
           from runs r join learnings l on l.run_id = r.id
           where r.completed_at is not null""")[0]
    fresh = _rows(
        """select count(*) as n, max(r.completed_at) as last
           from runs r join learnings l on l.run_id = r.id
           where r.completed_at > now() - %s * interval '1 minute'""", (STALL_MINUTES,))[0]
    fresh_cycle = (fresh["n"] or 0) > 0
    cycles_count = turning["n"] or 0
    latest_cycle = _rows(
        """select r.productive,
                  (select pa.rung from plan_acquisition pa
                   where pa.work_id=r.work_id and pa.status='completed'
                     and pa.completed_at=r.completed_at limit 1) as acquisition_rung
           from runs r join learnings l on l.run_id=r.id
           where r.completed_at is not null order by r.completed_at desc limit 1""")
    latest_cycle = latest_cycle[0] if latest_cycle else {}

    # AT_TARGET AMENDMENT: a fresh MEASUREMENT for the mission's metric proves we are still at
    # target. A heartbeat does NOT — it proves the process beat, not that the number held. At
    # target with a fresh measurement and no recent run is the satisfied loop (quiet is correct);
    # at target with a STALE measurement is STALLED, because we no longer know the number is there.
    fresh_meas = _rows(
        """select count(*) as n, max(taken_at) as last
           from measurements where metric=%s
           and taken_at > now() - %s * interval '1 minute'""", (metric, STALL_MINUTES))[0]
    fresh_measurement = (fresh_meas["n"] or 0) > 0
    latest_meas = _rows(
        "select taken_at, value, source from measurements where metric=%s "
        "order by taken_at desc limit 1", (metric,))
    latest_meas = latest_meas[0] if latest_meas else None

    # The heartbeat tells us what the process THINKS it is doing — but it can never satisfy the
    # loop-is-turning gate (that needs a completed run JOIN a learning), and per the amendment it
    # can never satisfy AT_TARGET either. It only distinguishes rate_limited-waiting from
    # rate_limited-overdue. retry_until compared against DB now(), never the client clock.
    heartbeat = _rows(
        """select state, beat_at, detail, retry_until,
                  retry_until > now() as retry_future,
                  beat_at > now() - %s * interval '1 minute' as fresh
           from heartbeat order by beat_at desc limit 1""", (STALL_MINUTES,))
    heartbeat = heartbeat[0] if heartbeat else None

    process_rows = _rows(
        """select pid, started_at, beat_at,
                  beat_at > now() - interval '8 seconds' as alive
           from loop_runtime where singleton=true""")
    process_runtime = process_rows[0] if process_rows else None
    parked = _rows(
        "select id, kind, summary, rationale, gate_reason, expected_cost_usd, "
        "blast_radius, created_at from work "
        "where status='awaiting_human' order by created_at")

    return {
        "mission": {
            "now": now_val,
            "error": measure_error,
            "target": target,
            "target_error": target_error,
            "goal": goal,
            "satisfied": satisfied,
            "overshooting": overshooting,
            "latest_measurement": latest_meas,
        },
        "loop": {
            "cycles": cycles_count,
            "last_cycle": turning["last"].isoformat() if turning["last"] else None,
            "last_age_min": int(turning["last_age_min"]) if turning["last_age_min"] is not None else None,
            "turning": fresh_cycle,
            "stall_minutes": STALL_MINUTES,
            "fresh_cycle": fresh_cycle,
            "fresh_measurement": fresh_measurement,
            "process_alive": bool(process_runtime and process_runtime["alive"]),
            "process_status": "running" if process_runtime and process_runtime["alive"] else "stopped",
            "process_pid": process_runtime["pid"] if process_runtime else None,
            "process_last_beat": process_runtime["beat_at"] if process_runtime else None,
            "last_cycle_productive": latest_cycle.get("productive"),
            "latest_acquisition_rung": latest_cycle.get("acquisition_rung"),
        },
        "heartbeat": heartbeat,
        # HIBERNATING is not STALLED. Stalled = something is wrong. Hibernating = the system did
        # the right thing, stopped spending, and is waiting for YOU. Showing them the same way
        # would train people to ignore the one state that actually requires them.
        "hibernation": _rows(
            """select h.id, h.hibernated_at, h.reason, h.unproductive, h.notified_at,
                      (select max(beat_at) from heartbeat where state='hibernating') as last_beat
               from hibernation h where h.resumed_at is null
               order by h.hibernated_at desc limit 1"""),
        "spend": _spend(),
        "parked": parked,
        "next": {
            "running": _rows(
                """select w.id, w.kind, w.summary, w.rationale, r.id as run_id, r.started_at
                   from work w join runs r on r.work_id = w.id
                   where w.status='running' and r.completed_at is null
                   order by r.started_at desc"""),
            "queued": _rows(
                """select id, kind, summary, rationale, created_at, approved_at
                   from work where status='pending'
                   order by approved_at desc nulls last, created_at limit 5"""),
        },
        "runs": _rows(
            """select r.id, r.succeeded, r.completed_at, r.outcome, r.cost_usd,
                      r.productive, r.evidence, r.measure_before, r.measure_after,
                      r.error, r.rolled_back, r.escalation_level, r.started_at,
                      w.kind, w.summary, w.rationale,
                      l.insight, l.scope, l.confidence, l.evidence_kind
               from runs r
               join work w on w.id = r.work_id
               left join learnings l on l.run_id = r.id
               where r.completed_at is not null
               order by r.completed_at desc limit 10"""),
        "learnings": _rows(
            "select id, insight, evidence, scope, confidence, evidence_kind, created_at from learnings "
            "where superseded_by is null order by created_at desc limit 20"),
        "beliefs_revised_count": _rows(
            "select count(*) as n from learnings where superseded_by is not null")[0]["n"],
        "trend": _rows(
            "select taken_at, value from measurements where metric=%s "
            "order by taken_at desc limit 40", (metric,)),
        "staged_imports": _rows(
            """select id, origin_instance, origin_mission, insight, confidence,
                      applies_when, falsified_by, received_at
               from shared_learnings where status='staged' order by received_at"""),
        "ladder": _ladder(),

        # THE BOARD. The bus must be inspectable from the UI — an inter-agent channel you cannot
        # read the history of will lie to you about what was said.
        "board": _rows(
            """select id, author, kind, title, body, created_at, supersedes
               from bb_notes
               where id not in (select supersedes from bb_notes where supersedes is not null)
               order by (kind='decision') desc, created_at desc limit 20"""),
        # THE BUS. delivered_at is the only thing that means delivered. Undelivered messages are
        # shown first and loudly: a handoff nobody picked up is how work silently dies.
        "bus": _rows(
            """select id, sender, recipient, subject, body, created_at,
                      delivered_at, handled_at
               from bb_messages order by (delivered_at is null) desc, created_at desc limit 20"""),
    }


def _spend() -> dict:
    """Spend vs caps. The metered trap (budget.py): in subscription mode monthly_hard==0 means
    'not applicable', NOT 'you may spend nothing'. Never show '$0 of $0'."""
    month = _rows(
        """select coalesce(sum(cost_usd),0) as month,
                  coalesce(sum(tokens_in+tokens_out),0) as tokens
           from runs where started_at >= date_trunc('month', now())""")[0]
    today = _rows(
        "select coalesce(sum(cost_usd),0) as today from runs "
        "where started_at >= date_trunc('day', now())")[0]
    return {"today": float(today["today"] or 0), "month": float(month["month"] or 0),
            "tokens": int(month["tokens"] or 0)}


def _ladder() -> dict:
    """Consecutive unproductive cycles, recomputed from the DB (not a counter in memory — one
    that resets on restart lets a stuck loop hammer forever by crashing). Maps to the escalation
    ladder in escalation.py: SELF_NUDGE_AT=3, PEER_AT=5, HUMAN_AT=8, HIBERNATE_AT=12."""
    rows = _rows(
        "select coalesce(productive, false) as productive from runs "
        "where completed_at is not null order by completed_at desc limit 14")
    streak = 0
    for r in rows:
        if r["productive"]:
            break
        streak += 1
    if streak >= 12:
        level = "hibernate"
    elif streak >= 8:
        level = "human"
    elif streak >= 5:
        level = "peer"
    elif streak >= 3:
        level = "self-nudge"
    else:
        level = "autonomous"
    return {"streak": streak, "level": level}


def _derive_health(*, mission_present, db_ok, cycles_count, fresh_cycle, fresh_measurement,
                   satisfied, now_val, target, hibernation, heartbeat, goal,
                   overshooting, stall_minutes, last_age_min,
                   last_cycle_productive=None, latest_acquisition_rung=None,
                   awaiting_human_count=0):
    """The seven-state machine, first match wins. Server-side — the page renders this verbatim.

    Order: NOT_ALIVE (no mission) > CANT_SEE (DB down) > NOT_ALIVE (no cycles) >
    WAITING_ON_YOU (hibernation) > WAITING_ON_PLAN (rate_limited, future) >
    OVERDUE (rate_limited, past) > AT_TARGET (satisfied + fresh measurement/cycle) >
    WORKING (fresh cycle) > STALLED.

    AT_TARGET uses fresh measurement/cycle, NOT heartbeat (bootstrap amendment). At target with a
    stale measurement falls through to STALLED — honest, because we no longer know the number held.
    """
    if not mission_present:
        return {"status": "NOT_ALIVE", "level": "amber", "icon": "○",
                "headline": "NOT ALIVE",
                "detail": "No mission — the interview was skipped or left incomplete. "
                          "An unaimed instance accomplishes nothing."}
    if not db_ok:
        return {"status": "CANT_SEE", "level": "grey", "icon": "?",
                "headline": "CAN'T SEE IT",
                "detail": "Can't read the database — including whether things are fine. "
                          "The panels below are stale; do not trust them as current."}
    if cycles_count == 0:
        return {"status": "NOT_ALIVE", "level": "amber", "icon": "○",
                "headline": "NOT ALIVE",
                "detail": "The loop has never completed a full cycle. Installed is not done."}
    if hibernation:
        h = hibernation[0]
        return {"status": "WAITING_ON_YOU", "level": "amber", "icon": "✋",
                "headline": "WAITING ON YOU",
                "detail": f"Paused — waiting on you. It got stuck after {h.get('unproductive')} "
                          f"unproductive cycles and stopped spending. Resumes when you approve "
                          f"below, not on a timer and not on a reboot."}
    if awaiting_human_count:
        return {"status": "WAITING_ON_YOU", "level": "amber", "icon": "✋",
                "headline": "WAITING ON YOU",
                "detail": f"{awaiting_human_count} gated plan(s) are waiting for approve or reject. "
                          "The target action has not run."}
    if heartbeat and heartbeat.get("state") == "rate_limited":
        if heartbeat.get("retry_future"):
            ru = heartbeat.get("retry_until")
            when = ru.isoformat() if ru else "unknown"
            return {"status": "WAITING_ON_PLAN", "level": "blue", "icon": "◔",
                    "headline": "WAITING FOR PLAN RESET",
                    "detail": f"Waiting for its plan to reset. Retries by {when}. "
                              f"This is expected on a subscription — not a failure."}
        return {"status": "OVERDUE", "level": "amber", "icon": "!",
                "headline": "OVERDUE",
                "detail": "The plan-reset deadline passed and the loop is still rate-limited. "
                          "Something is wrong."}
    # AT_TARGET — proven by a fresh measurement (the number is still there) or a fresh completed
    # cycle. NOT by a heartbeat. Quiet is correct here: a reach_and_maintain mission at target
    # honestly does nothing rather than manufacture work.
    if satisfied and (fresh_measurement or fresh_cycle):
        return {"status": "AT_TARGET", "level": "green", "icon": "✓",
                "headline": "AT TARGET — HOLDING",
                "detail": f"At target — holding ({_fmt_num(now_val)} of {_fmt_num(target)}). "
                          f"Quiet is correct here: the loop is maintaining, not pushing."}
    if fresh_cycle and latest_acquisition_rung:
        ago = f"{last_age_min}m ago" if last_age_min is not None else "recently"
        return {"status": "ACQUIRING", "level": "blue", "icon": "◔",
                "headline": "ACQUIRING EVIDENCE",
                "detail": f"Last full cycle {ago} completed the {latest_acquisition_rung} rung, "
                          "recorded its result, and kept the target plan pending."}
    if fresh_cycle and last_cycle_productive is False:
        ago = f"{last_age_min}m ago" if last_age_min is not None else "recently"
        return {"status": "REWORKING", "level": "amber", "icon": "!",
                "headline": "NO VERIFIED PROGRESS",
                "detail": f"Last full cycle {ago} was recorded and learned from, but independent "
                          "evaluation found no verified mission progress."}
    if fresh_cycle:
        ago = f"{last_age_min}m ago" if last_age_min is not None else "recently"
        return {"status": "WORKING", "level": "green", "icon": "●",
                "headline": "WORKING",
                "detail": f"Working. Last full cycle {ago} — acted, recorded, and learned."}
    ago = f"{last_age_min}m ago" if last_age_min is not None else "never"
    return {"status": "STALLED", "level": "red", "icon": "✕",
            "headline": "STALLED",
            "detail": f"No cycle finished in {stall_minutes} min — last was {ago}. "
                      f"The processes may be up; the loop is not turning."}


def _fmt_num(v):
    if v is None:
        return "—"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def state() -> dict:
    inst, mission, measure = _read_instance()
    mission_present = bool((mission.get("objective") or "").strip()) and bool((measure.get("what") or "").strip())

    # File-derived base — always available, even when the DB is down.
    budget = inst.get("budget") or {}
    money = budget.get("money") or {}
    autonomy = (budget.get("autonomy") or {}).get("level", "?")
    engine = inst.get("engine") or {}
    base = {
        "mission": {
            "objective": mission.get("objective", ""),
            "measure": measure.get("what", ""),
            "horizon": mission.get("horizon", ""),
            "now": None, "error": None, "target": None, "target_error": None,
            "goal": measure.get("goal", "reach_and_maintain"),
            "satisfied": False, "overshooting": False, "latest_measurement": None,
        },
        "engine": {
            "mode": engine.get("mode", "?"),
            "agent": engine.get("resident_agent", "?"),
            "autonomy": autonomy,
        },
        "budget": {
            "daily_soft_usd": money.get("daily_soft_usd", 0),
            "monthly_hard_usd": money.get("monthly_hard_usd", 0),
            # A hard cap may remain configured in subscription mode as a safety belt for an
            # explicit future mode switch. Billing mode comes from the executor config, never
            # from whether the cap is nonzero.
            "metered": engine.get("mode", "api") == "api",
        },
        "xss_canary": _XSS_CANARY,
    }

    # No mission -> NOT_ALIVE. Detectable from the file alone; no DB needed.
    if not mission_present:
        base["health"] = _derive_health(
            mission_present=False, db_ok=False, cycles_count=0, fresh_cycle=False,
            fresh_measurement=False, satisfied=False, now_val=None, target=None,
            hibernation=None, heartbeat=None, goal="reach_and_maintain",
            overshooting=False, stall_minutes=STALL_MINUTES, last_age_min=None)
        base["loop"] = {"cycles": 0, "last_cycle": None, "turning": False,
                        "stall_minutes": STALL_MINUTES, "fresh_cycle": False,
                        "fresh_measurement": False, "last_age_min": None}
        base["spend"] = {"today": 0, "month": 0, "tokens": 0}
        base["parked"] = []
        base["next"] = {"running": [], "queued": []}
        base["runs"] = []
        base["learnings"] = []
        base["trend"] = []
        base["staged_imports"] = []
        base["ladder"] = {"streak": 0, "level": "autonomous"}
        base["board"] = []
        base["bus"] = []
        base["hibernation"] = []
        base["heartbeat"] = None
        base["beliefs_revised_count"] = 0
        return base

    # Try the DB. Connection-level failure -> CANT_SEE, with prior payload DIMMED (not green).
    global _LAST_GOOD, _LAST_GOOD_AT
    try:
        live = _db_state(measure, mission)
    except Exception as e:
        stale = _LAST_GOOD or {}
        full = {**base, **stale}
        # Deep-merge mission: base has fresh file-derived fields (objective, horizon), stale has
        # the DB-derived ones. Shallow merge would drop the objective on a CANT_SEE refresh.
        full["mission"] = {**base["mission"], **(stale.get("mission") or {})}
        full["health"] = _derive_health(
            mission_present=True, db_ok=False, cycles_count=stale.get("loop", {}).get("cycles", 0),
            fresh_cycle=False, fresh_measurement=False,
            satisfied=stale.get("mission", {}).get("satisfied", False),
            now_val=stale.get("mission", {}).get("now"), target=stale.get("mission", {}).get("target"),
            hibernation=stale.get("hibernation"), heartbeat=stale.get("heartbeat"),
            goal=base["mission"]["goal"], overshooting=False,
            stall_minutes=STALL_MINUTES, last_age_min=None)
        # Override: DB-down is always CANT_SEE, even if the last good payload was green.
        full["health"] = {"status": "CANT_SEE", "level": "grey", "icon": "?",
                          "headline": "CAN'T SEE IT",
                          "detail": f"Can't read the database — including whether things are fine. "
                                    f"{str(e).strip().split(chr(10))[0][:140]} "
                                    f"The panels below are stale; do not trust them as current."}
        full["_stale"] = True
        full["_last_good_at"] = _LAST_GOOD_AT.isoformat() if _LAST_GOOD_AT else None
        return full

    full = {**base, **live}
    # Deep-merge mission: base carries file-derived fields (objective, measure name, horizon);
    # live carries DB-derived fields (now, target, satisfied, ...). Shallow {**base, **live}
    # replaces the whole mission dict and loses the objective — that is the bug this fixes.
    full["mission"] = {**base["mission"], **live.get("mission", {})}
    full["health"] = _derive_health(
        mission_present=True, db_ok=True,
        cycles_count=full["loop"]["cycles"], fresh_cycle=full["loop"]["fresh_cycle"],
        fresh_measurement=full["loop"]["fresh_measurement"],
        satisfied=full["mission"]["satisfied"], now_val=full["mission"]["now"],
        target=full["mission"]["target"], hibernation=full["hibernation"],
        heartbeat=full["heartbeat"], goal=full["mission"]["goal"],
        overshooting=full["mission"]["overshooting"],
        stall_minutes=STALL_MINUTES, last_age_min=full["loop"].get("last_age_min"),
        last_cycle_productive=full["loop"].get("last_cycle_productive"),
        latest_acquisition_rung=full["loop"].get("latest_acquisition_rung"),
        awaiting_human_count=len(full.get("parked") or []))
    _LAST_GOOD = full
    _LAST_GOOD_AT = datetime.now(timezone.utc)
    return full


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            # state() never raises now — it returns CANT_SEE on DB failure. A 200 with a CANT_SEE
            # body is the honest answer; a 500 would make the client show a blank error instead
            # of degrading to grey.
            self._send(200, json.dumps(state(), default=_json_safe))
        elif self.path == "/favicon.ico":
            # A 404 in the console trains people to ignore the console. Answer it.
            self._send(204, b"", "image/x-icon")
        elif self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        # Approving parked work is the ONE thing the human does here, so it is the one
        # write this server accepts.
        if self.path.startswith("/api/approve/"):
            try:
                authorized, reason = _approval_authorized(self.headers)
                if not authorized:
                    self._send(403, json.dumps({"approved": False, "error": reason}))
                    return
                wid = int(self.path.rsplit("/", 1)[1])
                with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "UPDATE work SET status='pending', approved_at=now() "
                        "WHERE id=%s AND status='awaiting_human' "
                        "RETURNING *", (wid,))
                    row = cur.fetchone()
                ok = row is not None
                if ok:
                    assert_valid_approval(row)
                # 409 "changed underneath you" — guardrail 8: one write stays one write, with the
                # awaiting_human guard intact. Re-approving an already-approved row is a conflict.
                self._send(200 if ok else 409,
                           json.dumps({"approved": ok, "id": wid}))
            except ApprovalInvalid as e:
                self._send(500, json.dumps({"approved": False, "error": str(e)}))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *a):
        pass  # the loop's log is the interesting one, not this


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>autonomy-quest</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root{
    --paper:#EDF0EE; --paper-2:#E3E8E5; --ink:#141F1B; --soft:#4B5A55; --faint:#84948E;
    --rule:#CBD4D0; --accent:#0B6E52; --accent-w:#E2EDE8; --warn:#A85410; --warn-w:#F3E7DC;
    --blue:#2D5F8F; --blue-w:#DDE8F2; --red:#A02828; --red-w:#F2DCDC; --grey:#84948E; --grey-w:#E0E4E1;
    --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    --body:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
    --display:"Iowan Old Style",Charter,Georgia,serif;
  }
  @media (prefers-color-scheme:dark){:root{
    --paper:#0C1512; --paper-2:#131F1A; --ink:#DEE7E2; --soft:#9AAAA3; --faint:#67776F;
    --rule:#26332E; --accent:#4FD1A5; --accent-w:#10241C; --warn:#E08843; --warn-w:#2A1B0F;
    --blue:#6FA8DC; --blue-w:#0F2030; --red:#E07070; --red-w:#2A1010; --grey:#67776F; --grey-w:#181F1C;}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--body);line-height:1.6}
  body.stale section{opacity:.45;transition:opacity .3s}
  .wrap{max-width:980px;margin:0 auto;padding:2.5rem 1.25rem 5rem}
  h1{font-family:var(--display);font-size:1.5rem;margin:0;letter-spacing:-.01em}
  .sub{font-family:var(--mono);font-size:.7rem;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);margin-bottom:.4rem}
  .identity{font-family:var(--mono);font-size:.72rem;color:var(--soft);margin-top:.4rem}
  section{border-top:1px solid var(--rule);padding:1.75rem 0}
  h2{font-family:var(--display);font-size:1.05rem;margin:0 0 1rem}
  .eyebrow{font-family:var(--mono);font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin-bottom:.5rem}

  /* the health banner — the answer to "is it working?" */
  .live{display:flex;align-items:center;gap:.8rem;padding:1rem 1.2rem;border:1px solid var(--rule);
        background:var(--paper-2);border-left:4px solid var(--accent);margin:1.25rem 0}
  .live.amber{border-left-color:var(--warn);background:var(--warn-w)}
  .live.blue{border-left-color:var(--blue);background:var(--blue-w)}
  .live.red{border-left-color:var(--red);background:var(--red-w)}
  .live.grey{border-left-color:var(--grey);background:var(--grey-w)}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--accent);flex:none}
  .live.amber .dot{background:var(--warn)} .live.blue .dot{background:var(--blue)}
  .live.red .dot{background:var(--red)} .live.grey .dot{background:var(--grey)}
  .live .icon{font-size:1.1rem;flex:none}
  .live b{font-weight:600}
  .live .why{color:var(--soft);font-size:.9rem}

  /* mission panel */
  .miss{display:flex;align-items:baseline;gap:.6rem;flex-wrap:wrap}
  .miss .big{font-family:var(--display);font-size:2.2rem;font-variant-numeric:tabular-nums;line-height:1}
  .miss .of{color:var(--faint);font-size:1.1rem}
  .bar{position:relative;height:10px;background:var(--paper-2);border:1px solid var(--rule);border-radius:2px;margin:.7rem 0;overflow:visible}
  .bar-fill{position:absolute;left:0;top:0;bottom:0;background:var(--accent);border-radius:1px;transition:width .4s}
  .bar.over .bar-fill{background:var(--warn)}
  .bar-cap{font-family:var(--mono);font-size:.66rem;color:var(--faint);margin-top:.3rem}
  .bar-cap.warn{color:var(--warn)}
  .spark{vertical-align:middle;margin-left:.4rem}
  .src{font-family:var(--mono);font-size:.66rem;color:var(--faint);margin-top:.3rem;word-break:break-all}

  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1px;background:var(--rule);border:1px solid var(--rule)}
  .cell{background:var(--paper);padding:.9rem 1rem}
  .k{font-family:var(--mono);font-size:.63rem;letter-spacing:.1em;text-transform:uppercase;color:var(--faint)}
  .v{font-family:var(--display);font-size:1.35rem;font-variant-numeric:tabular-nums;margin-top:.15rem}
  .v small{font-size:.78rem;color:var(--faint)}
  .mini{height:5px;background:var(--paper-2);border:1px solid var(--rule);border-radius:1px;margin-top:.3rem;position:relative}
  .mini-fill{position:absolute;left:0;top:0;bottom:0;background:var(--accent)}
  .mini-fill.amber{background:var(--warn)} .mini-fill.red{background:var(--red)}
  .ladder{font-family:var(--mono);font-size:.7rem;letter-spacing:.1em}
  .ladder .on{color:var(--accent)} .ladder .off{color:var(--faint)}

  .park{border:1px solid var(--warn);background:var(--warn-w);padding:1rem 1.2rem;margin-bottom:.75rem}
  .park.stale-age{border-width:2px}
  .park h3{margin:0 0 .3rem;font-size:.98rem;font-family:var(--display)}
  .park p{margin:.3rem 0 .8rem;color:var(--soft);font-size:.9rem}
  button{font-family:var(--body);font-size:.85rem;font-weight:600;padding:.45rem 1rem;border:1px solid var(--accent);
         background:var(--accent);color:var(--paper);cursor:pointer}
  button:hover{opacity:.88} button:focus-visible{outline:2px solid var(--ink);outline-offset:2px}

  .next-item{padding:.6rem 0;border-bottom:1px solid var(--rule)}
  .next-item:last-child{border-bottom:0}
  .next-item .sm{font-size:.92rem}
  .next-item .out{color:var(--soft);font-size:.85rem;margin-top:.15rem}

  .row{border-bottom:1px solid var(--rule);padding:.85rem 0;display:grid;grid-template-columns:2.6rem 1fr;gap:.9rem}
  .row:last-child{border-bottom:0}
  .id{font-family:var(--mono);font-size:.78rem;color:var(--faint);padding-top:.15rem}
  .sm{font-size:.9rem}
  .out{color:var(--soft);font-size:.86rem;margin-top:.2rem}
  .phases{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:.7rem;margin-top:.5rem}
  .phase{padding:.5rem .6rem;background:var(--paper-2);border-radius:3px;font-size:.82rem}
  .phase .lab{font-family:var(--mono);font-size:.6rem;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);display:block;margin-bottom:.2rem}
  .phase.warn{background:var(--warn-w)}
  .learn{margin-top:.45rem;padding-left:.7rem;border-left:2px solid var(--accent);font-size:.86rem}
  .learn em{color:var(--faint);font-style:normal;font-family:var(--mono);font-size:.7rem;letter-spacing:.06em;text-transform:uppercase}
  .tag{font-family:var(--mono);font-size:.62rem;letter-spacing:.08em;text-transform:uppercase;padding:.1rem .4rem;border:1px solid currentColor}
  .ok{color:var(--accent)} .bad{color:var(--warn)} .blue{color:var(--blue)}
  .empty{color:var(--faint);font-size:.9rem}
  .legend{font-size:.86rem;color:var(--soft)}
  .legend table{border-collapse:collapse;margin:.5rem 0}
  .legend td{padding:.2rem .6rem;border:1px solid var(--rule);font-size:.82rem}
  .legend td:first-child{font-family:var(--mono);white-space:nowrap}
  .xss{font-family:var(--mono);font-size:.7rem;color:var(--soft);word-break:break-all;background:var(--paper-2);padding:.5rem .7rem;border-radius:3px;margin-top:.3rem}
  .xss code{font-family:var(--mono)}
  .footer-note{font-family:var(--mono);font-size:.66rem;color:var(--faint);margin-top:2rem;text-align:center}
</style></head><body>
<div class="wrap">
  <div class="sub">autonomy-quest</div>
  <h1 id="obj">…</h1>
  <div class="identity" id="identity"></div>

  <div class="live" id="live"><span class="dot"></span><span class="icon" id="liveicon"></span><span id="livetext"></span></div>

  <section id="missionsec">
    <div class="eyebrow">The mission's number</div>
    <h2 id="missh">Where the mission stands</h2>
    <div id="mission"></div>
  </section>

  <section id="parksec" hidden>
    <div class="eyebrow">Waiting on you</div>
    <h2>It stopped and asked, rather than acting</h2>
    <div id="parked"></div>
  </section>

  <section>
    <div class="eyebrow">Vitals</div>
    <h2>At a glance</h2>
    <div class="grid" id="stats"></div>
  </section>

  <section>
    <div class="eyebrow">Up next</div>
    <h2>What it is doing, and what is queued</h2>
    <div id="next"></div>
  </section>

  <section>
    <div class="eyebrow">The loop</div>
    <h2>What it did, and what it learned from it</h2>
    <div id="runs"></div>
  </section>

  <section>
    <div class="eyebrow">What it believes</div>
    <h2>Learnings still in force</h2>
    <div id="learnings"></div>
    <div id="staged"></div>
  </section>

  <section id="boardsec">
    <div class="eyebrow">The board</div>
    <h2>Decisions and notes &mdash; what the agents told each other</h2>
    <div id="board"></div>
  </section>

  <section id="bussec">
    <div class="eyebrow">The bus</div>
    <h2>Agent-to-agent messages</h2>
    <p class="empty" style="margin-top:-.5rem">A message is <em>delivered</em> only when someone actually read it. Undelivered ones are shown first &mdash; a handoff nobody picked up is how work silently dies.</p>
    <div id="bus"></div>
  </section>

  <section>
    <div class="eyebrow">How to read this page</div>
    <details><summary>The banner, the loop, and the words (click to open)</summary>
      <div class="legend" id="legend"></div>
    </details>
  </section>

  <section>
    <div class="eyebrow">Security proof</div>
    <p class="sm" style="color:var(--soft)">Every string from the model or database is escaped through <code>esc()</code> before <code>innerHTML</code>. The payload below is data, not code &mdash; it goes through the same path every database field takes. If you see it as text, the escaping held.</p>
    <div class="xss">canary: <code id="xss-canary"></code><br><span id="xss-status">checking…</span></div>
  </section>

  <div class="footer-note" id="footernote">local window — anyone who can reach this port sees everything</div>
</div>
<script>
// esc() escapes every character that could break out of a text node or attribute in innerHTML.
// '& < > "' are the innerHTML/attribute breakout set; "'" is added for single-quoted attributes.
// This is the ONLY function that touches raw DB/model text before innerHTML. Everything routes here.
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const ago = iso => {
  if(!iso) return "never";
  const m = Math.floor((Date.now() - new Date(iso))/60000);
  if (m < 1) return "just now";
  if (m < 60) return m + "m ago";
  const h = Math.floor(m/60); if (h < 24) return h + "h ago";
  return Math.floor(h/24) + "d ago";
};
const num = v => (v === null || v === undefined) ? "—" : (typeof v === "number" && Number.isInteger(v) ? v : v);
const AUTONOMY_WORDS = {propose:"asks before everything","act-reversible":"acts alone only when undoable",
  "act-external":"acts alone except money & code","act-broad":"acts alone inside its boundaries"};

function sparkline(points){
  if(!points || points.length < 2) return '<span class="empty" style="font-size:.78rem">not enough samples yet</span>';
  const w=120,h=28, vals=points.map(p=>+p.value), min=Math.min(...vals), max=Math.max(...vals), range=(max-min)||1;
  // oldest -> newest (points come newest-first from the API)
  const pts = points.slice().reverse().map((p,i)=>{
    const x=(i/(points.length-1))*w, y=h-((+p.value-min)/range)*h;
    return x.toFixed(1)+","+y.toFixed(1);
  }).join(" ");
  return '<svg width="'+w+'" height="'+h+'" class="spark"><polyline points="'+pts+'" fill="none" stroke="var(--accent)" stroke-width="1.5"/></svg>';
}
function missionBar(now, target, goal, overshooting){
  if(goal==="maximize") return '<div class="bar-cap">more is the point — no ceiling. Trend shown.</div>';
  if(target===null || target===undefined) return '<div class="bar-cap warn">No target set — verify.sh fails measures without a ceiling.</div>';
  if(now===null || now===undefined) return '<div class="bar-cap warn">Can\'t read the number right now.</div>';
  const pct = Math.min(100, (now/target)*100);
  const over = overshooting ? " over" : "";
  const cap = overshooting ? "past target — being checked" : "target — then hold";
  return '<div class="bar'+over+'"><div class="bar-fill" style="width:'+pct.toFixed(1)+'%"></div></div>'
    + '<div class="bar-cap'+(overshooting?' warn':'')+'">'+cap+'</div>';
}

async function tick(){
  const s = await (await fetch("/api/state")).json();
  // CANT_SEE degrades: dim the stale panels, never keep green on data we can't verify.
  document.body.classList.toggle("stale", !!s._stale);
  const fn = document.getElementById("footernote");
  fn.textContent = s._last_good_at
    ? "local window — last good data " + ago(s._last_good_at) + " (currently can't reach the database)"
    : "local window — anyone who can reach this port sees everything";

  document.getElementById("obj").textContent = s.mission.objective || "(no mission — the interview was skipped)";
  document.getElementById("identity").textContent =
    [s.engine.agent, s.engine.mode, AUTONOMY_WORDS[s.engine.autonomy]||s.engine.autonomy, s.mission.horizon]
    .filter(Boolean).join(" · ");

  // HEALTH BANNER — rendered verbatim from the server-side state machine (guardrail 7).
  const live = document.getElementById("live"), t = document.getElementById("livetext"),
        ic = document.getElementById("liveicon");
  const h = s.health || {status:"?",level:"grey",icon:"?",headline:"?",detail:""};
  live.className = "live " + (h.level||"grey");
  ic.textContent = h.icon || "";
  t.innerHTML = "<b>" + esc(h.headline) + "</b> <span class='why'>" + esc(h.detail) + "</span>";

  // MISSION PANEL
  const m = s.mission;
  const nowStr = num(m.now), tgtStr = num(m.target);
  const goalWord = m.goal === "maximize" ? "more is the point" : "reach it, then hold it";
  let missHTML = '<div class="miss"><span class="big">' + esc(nowStr) + '</span>'
    + '<span class="of">of ' + esc(tgtStr) + '</span></div>'
    + missionBar(m.now, m.target, m.goal, m.overshooting)
    + '<div class="bar-cap">goal: ' + esc(goalWord) + '</div>'
    + sparkline(s.trend);
  if(m.latest_measurement){
    missHTML += '<div class="src">last sample: ' + esc(m.latest_measurement.value) + ' at ' + esc(ago(m.latest_measurement.taken_at)) + ' from ' + esc(m.latest_measurement.source) + '</div>';
  }
  if(m.error){ missHTML += '<div class="bar-cap warn">measure error: ' + esc(m.error) + '</div>'; }
  if(m.target_error){ missHTML += '<div class="bar-cap warn">target error: ' + esc(m.target_error) + '</div>'; }
  document.getElementById("mission").innerHTML = missHTML;

  // VITALS STRIP
  const sp = s.spend || {today:0,month:0};
  const b = s.budget || {};
  let spendCell;
  if(!b.metered){
    spendCell = '<div class="cell"><div class="k">spend</div><div class="v" style="font-size:1rem">flat-rate plan<small><br>limited by usage resets, not dollars</small></div></div>';
  } else {
    const monthPct = Math.min(100, (sp.month/(b.monthly_hard_usd||1))*100);
    const monthCls = monthPct>=100?'red':(monthPct>=80?'amber':'');
    const todayPct = Math.min(100, (sp.today/(b.daily_soft_usd||1))*100);
    spendCell = '<div class="cell"><div class="k">spend today / month</div>'
      + '<div class="v">$'+Number(sp.today||0).toFixed(2)+' <small>of $'+esc(b.daily_soft_usd)+'</small></div>'
      + '<div class="mini"><div class="mini-fill" style="width:'+todayPct.toFixed(1)+'%"></div></div>'
      + '<div class="v" style="font-size:1.1rem;margin-top:.4rem">$'+Number(sp.month||0).toFixed(2)+' <small>of $'+esc(b.monthly_hard_usd)+'</small></div>'
      + '<div class="mini"><div class="mini-fill '+monthCls+'" style="width:'+monthPct.toFixed(1)+'%"></div></div></div>';
  }
  const ld = s.ladder || {streak:0,level:"autonomous"};
  const LADDER = ["autonomous","self-nudge","peer","human","hibernate"];
  const lvlIdx = LADDER.indexOf(ld.level);
  const dots = LADDER.map((_,i)=>'<span class="'+(i<=lvlIdx?'on':'off')+'">●</span>').join(" ");
  document.getElementById("stats").innerHTML =
    '<div class="cell"><div class="k">'+(esc(s.mission.measure)||'measure')+'</div><div class="v">'+esc(nowStr)+'</div></div>'
    + '<div class="cell"><div class="k">cycles</div><div class="v">'+(s.loop.cycles||0)+'</div><div class="out">last '+ago(s.loop.last_cycle)+'</div></div>'
    + spendCell
    + '<div class="cell"><div class="k">engine</div><div class="v" style="font-size:1rem">'+esc(s.engine.agent)+'<br><small>'+esc(s.engine.autonomy)+'</small></div></div>'
    + '<div class="cell"><div class="k">ladder · '+esc(ld.level)+'</div><div class="ladder">'+dots+'<br><small style="color:var(--faint)">'+ld.streak+' unproductive</small></div></div>';

  // UP NEXT
  const nx = s.next || {running:[],queued:[]};
  let nextHTML = '';
  if(nx.running && nx.running.length){
    nextHTML += '<div class="eyebrow" style="color:var(--accent)">Now doing</div>';
    nextHTML += nx.running.map(r=>'<div class="next-item"><div class="sm"><span class="tag ok">running</span> &nbsp;'+esc(r.summary)+'</div><div class="out">'+esc(r.rationale)+' — started '+ago(r.started_at)+'</div></div>').join("");
  } else {
    nextHTML += '<div class="eyebrow" style="color:var(--faint)">Now doing</div><p class="empty">Nothing running right now.</p>';
  }
  if(nx.queued && nx.queued.length){
    nextHTML += '<div class="eyebrow" style="margin-top:.8rem">Queued</div>';
    nextHTML += nx.queued.map(q=>'<div class="next-item"><div class="sm">'+esc(q.summary)+'</div><div class="out">'+esc(q.kind)+' — '+ago(q.created_at)+(q.approved_at?(' · approved '+ago(q.approved_at)):'')+'</div></div>').join("");
  } else {
    const atTgt = (s.health||{}).status === "AT_TARGET";
    nextHTML += '<div class="eyebrow" style="margin-top:.8rem">Queued</div><p class="empty">'
      + (atTgt ? 'Nothing queued — at target and holding.' : 'Nothing queued — the next cycle decides when it runs.') + '</p>';
  }
  document.getElementById("next").innerHTML = nextHTML;

  // WAITING ON YOU — parked work + hibernation context. Hidden when empty.
  const ps = document.getElementById("parksec"), pd = document.getElementById("parked");
  const parked = s.parked || [];
  if(parked.length){
    ps.hidden = false;
    const hib = (s.hibernation||[])[0];
    let html = '';
    if(hib){
      html += '<p class="sm" style="color:var(--warn);margin-top:0">Hibernating since '+ago(hib.hibernated_at)+' — '+esc(hib.reason)+' ('+hib.unproductive+' unproductive cycles).</p>';
    }
    html += parked.map(w=>{
      const ageH = Math.floor((Date.now()-new Date(w.created_at))/3600000);
      return '<div class="park'+(ageH>=24?' stale-age':'')+'"><h3>'+esc(w.summary)+'</h3><p>'+esc(w.rationale)+'</p>'
        + '<button onclick="approve('+w.id+')">Approve — let it proceed</button></div>';
    }).join("");
    pd.innerHTML = html;
  } else {
    ps.hidden = true;
  }

  // THE LOOP — each cycle as observe → decide → act → record → learn
  const runs = s.runs || [];
  document.getElementById("runs").innerHTML = runs.length ? runs.map(r=>{
    const orphaned = (r.outcome||"").indexOf("ORPHANED:")===0;
    const acted = r.succeeded===true ? '<span class="tag ok">done</span>'
      : (r.succeeded===false ? '<span class="tag bad">failed</span>' : '<span class="tag">?</span>');
    const productiveTag = r.productive===true ? '<span class="tag ok" title="produced something real">productive</span>'
      : (r.productive===false ? '<span class="tag bad" title="nothing real produced">narrated</span>' : '');
    let phases = '<div class="phases">'
      + '<div class="phase"><span class="lab">observed</span>'+(r.measure_before!==null&&r.measure_before!==undefined?esc(r.measure_before):'—')+'</div>'
      + '<div class="phase"><span class="lab">decided</span>'+esc(r.kind)+(r.summary?' · '+esc(r.summary):'')+'</div>'
      + '<div class="phase'+(r.rolled_back||r.succeeded===false?' warn':'')+'"><span class="lab">acted</span>'+acted+(r.rolled_back?' <span class="tag bad">rolled back</span>':'')+'</div>'
      + '<div class="phase"><span class="lab">recorded</span>'+productiveTag+' '+(r.outcome?esc(r.outcome):'—')+'</div>'
      + '<div class="phase'+(r.insight?'':' warn')+'"><span class="lab">learned</span>'+(r.insight?esc(r.insight):'no learning recorded')+'</div>'
      + '</div>';
    if(orphaned){
      phases = '<div class="phases"><div class="phase warn" style="grid-column:1/-1"><span class="lab">interrupted</span>'+esc(r.outcome)+'</div></div>';
    }
    return '<div class="row"><div class="id">#'+r.id+'</div><div>'
      + '<div class="sm">'+esc(r.summary)+'</div>'
      + (r.error?'<div class="out" style="color:var(--warn)">error: '+esc(r.error)+'</div>':'')
      + phases
      + (r.escalation_level?'<div class="out">escalation: '+esc(r.escalation_level)+'</div>':'')
      + '</div></div>';
  }).join("") : '<div class="empty">No cycles yet — that is what the first cycle is for.</div>';

  // WHAT IT BELIEVES
  const lns = s.learnings || [];
  document.getElementById("learnings").innerHTML = lns.length ? lns.map(l=>{
    const conf = l.confidence===undefined?'':(l.confidence<0.4?'hunch':(l.confidence<=0.7?'probably':'confident'));
    return '<div class="row"><div class="id">'+(l.scope==='generalisable'?'↗':'·')+'</div><div>'
      + '<div class="sm">'+esc(l.insight)+'</div>'
      + '<div class="out">'+esc(l.evidence)+'</div>'
      + '<div class="out"><span class="tag">'+esc(l.scope==='generalisable'?'could help other missions':'applies here')+'</span> '
      + (conf?'<span class="tag">'+esc(conf)+'</span>':'')+'</div></div></div>';
  }).join("") : '<div class="empty">Nothing learned yet — that is what the first cycle is for.</div>';

  const staged = s.staged_imports || [];
  document.getElementById("staged").innerHTML = staged.length
    ? '<div class="eyebrow" style="margin-top:1rem">Staged imports (inert)</div>'
      + '<p class="sm" style="color:var(--soft);margin-top:-.3rem">'+staged.length+' idea(s) arrived from other instances. INERT until reviewed via <code>./aq.py share review</code>.</p>'
      + staged.map(si=>'<div class="row"><div class="id">↗</div><div><div class="sm">'+esc(si.insight)+'</div><div class="out">from '+esc(si.origin_instance)+' (confidence '+si.confidence+')</div></div></div>').join("")
    : '';

  document.getElementById("board").innerHTML = (s.board||[]).length ? s.board.map(n =>
    '<div class="row"><div class="id">'+(n.kind==='decision'?'✓':'·')+'</div><div>'
    + '<div class="sm"><span class="tag '+(n.kind==='decision'?'ok':'')+'">'+esc(n.kind)+'</span> &nbsp;'+esc(n.title)+' <span class="out"> &nbsp;&mdash; '+esc(n.author)+'</span></div>'
    + '<div class="out">'+esc(n.body)+'</div></div></div>').join("")
    : '<div class="empty">Nothing on the board yet.</div>';

  document.getElementById("bus").innerHTML = (s.bus||[]).length ? s.bus.map(m =>
    '<div class="row"><div class="id">#'+m.id+'</div><div>'
    + '<div class="sm"><span class="tag '+(m.delivered_at?'ok':'bad')+'">'+(m.delivered_at?'delivered':'UNREAD')+'</span> &nbsp;'+esc(m.sender)+' &rarr; '+esc(m.recipient)+': '+esc(m.subject)+'</div>'
    + '<div class="out">'+esc(m.body)+'</div>'
    + (m.handled_at?'<div class="out" style="color:var(--accent)">acted on '+ago(m.handled_at)+'</div>':'')
    + '</div></div>').join("")
    : '<div class="empty">No messages. (Single-agent instance — the bus matters once there is more than one.)</div>';

  // LEGEND
  document.getElementById("legend").innerHTML =
    '<p><b>The banner:</b> CAN\'T SEE IT (can\'t read the DB) · NOT ALIVE (no completed cycle) · '
    + 'WAITING ON YOU (hibernating, needs you) · WAITING FOR PLAN RESET (rate-limited, retry coming) · '
    + 'OVERDUE (retry deadline passed) · AT TARGET (holding, quiet is correct) · WORKING (turning) · '
    + 'STALLED (no cycle in the stall window).</p>'
    + '<table><tr><td>cycle</td><td>a full loop: observe → decide → act → record → learn</td></tr>'
    + '<tr><td>measure</td><td>the mission\'s number, re-read from its real source every time</td></tr>'
    + '<tr><td>reach_and_maintain</td><td>reach the target, then hold it</td></tr>'
    + '<tr><td>maximize</td><td>more is the point — no ceiling</td></tr>'
    + '<tr><td>productive</td><td>produced something real, not just narrated</td></tr>'
    + '<tr><td>stalled</td><td>no cycle finished in the stall window</td></tr>'
    + '<tr><td>ladder</td><td>how it asks for help: autonomous → self-nudge → peer → human → hibernate</td></tr>'
    + '<tr><td>soft cap</td><td>slow-down line (daily)</td></tr>'
    + '<tr><td>hard cap</td><td>stop line (monthly) — the loop halts</td></tr></table>';

  // XSS PROOF — the canary goes through the SAME esc() every DB field uses. If it renders as
  // text, the escaping held. The assertion below confirms no script executed and no image fired.
  const canary = document.getElementById("xss-canary");
  canary.innerHTML = esc(s.xss_canary);
  // __xss_FIRED is set ONLY if a <script> ran or an <img onerror> fired. Inert => undefined.
  setTimeout(function(){
    var st = document.getElementById("xss-status");
    if(window.__xss_FIRED){
      st.textContent = "FAIL: payload executed (" + window.__xss_FIRED + ") — escaping is broken";
      st.style.color = "var(--red)";
    } else {
      st.textContent = "inert — no script executed, no image fired. Escaping held.";
      st.style.color = "var(--accent)";
    }
  }, 60);
}
async function approve(id){
  let token = localStorage.getItem("aqApprovalToken") || "";
  if(!token){
    token = prompt("Approval token");
    if(!token){ return; }
    localStorage.setItem("aqApprovalToken", token);
  }
  let r = await fetch("/api/approve/"+id, {method:"POST", headers:{"X-AQ-Approval-Token":token}});
  if(r.status === 403){
    localStorage.removeItem("aqApprovalToken");
    token = prompt("Approval token rejected or not configured. Enter the approval token");
    if(!token){ return; }
    localStorage.setItem("aqApprovalToken", token);
    r = await fetch("/api/approve/"+id, {method:"POST", headers:{"X-AQ-Approval-Token":token}});
  }
  if(r.status === 409){ alert("That changed underneath you — it may already be approved. Refreshing."); }
  tick();
}
tick(); setInterval(tick, 5000);
</script></body></html>"""


def serve(port: int = 8080) -> None:
    # Default 127.0.0.1 (guardrail 9: local window, no login system). Override AQ_UI_BIND for a
    # container that needs to expose the port. "Anyone who can reach this port sees everything."
    host = os.environ.get("AQ_UI_BIND", "127.0.0.1")
    print(f"[aq] UI on http://{host}:{port}  (local window — anyone who can reach this port sees everything)")
    HTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    serve(int(os.environ.get("AQ_UI_PORT", "8080")))
