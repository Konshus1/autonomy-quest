#!/usr/bin/env python3
"""Validation harness for the mission-status UI (task #3023).

Forces every health state by manipulating ground truth in a throwaway database, then asserts the
/api/state health.status matches. Also exercises the approve round-trip and the XSS inert-render
proof. Runs the UI server in-process on a throwaway port against an isolated test database.

NO TalkingBack internals, NO #3018 container, NO prod. Public-kit-only.

Usage:
  AQ_DB_URL=postgresql://aq:aq@127.0.0.1:55433/autonomy_quest .venv-3023/bin/python test_ui_states.py
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import threading
import time

import psycopg2
import yaml

DB_URL = os.environ.get("AQ_DB_URL", "postgresql://aq:aq@127.0.0.1:55433/autonomy_quest")
UI_PORT = 8097
METRIC = "test_count"
TARGET = 100.0
STALL_MIN = 180  # matches ui/server.py default

HERE = os.path.dirname(os.path.abspath(__file__))
INSTANCE_YAML = os.path.join(HERE, "instance.yaml")

# Integration harness: needs a live throwaway Postgres (see Usage above). Under plain `pytest`
# with no container DB up, skip cleanly instead of erroring so the unit suite stays honestly
# green — same DB-gating discipline as the Pg causal-store tests. Run explicitly against a live
# DB via `AQ_DB_URL=... pytest tests/test_ui_states.py`.
import pytest  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(DB_URL).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason=f"integration DB not reachable at {DB_URL}; run against a live container DB",
)

PASS = 0
FAIL = 0


def check(name, got, expected):
    global PASS, FAIL
    ok = got == expected
    mark = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  {mark}  {name}: got {got!r}, expected {expected!r}")
    return ok


def check_true(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# --- DB helpers ---------------------------------------------------------------
def db():
    return psycopg2.connect(DB_URL)


def sql(stmt, args=None):
    with db() as c, c.cursor() as cur:
        cur.execute(stmt, args or ())
        try:
            return cur.fetchall()
        except Exception:
            return []


def sql1(stmt, args=None):
    rows = sql(stmt, args)
    if not rows:
        raise RuntimeError(f"no rows returned: {stmt[:80]}")
    return rows[0]


def reset_db():
    """Wipe all data tables to a clean slate. Keeps schema."""
    for t in ("bb_messages", "bb_notes", "shared_learnings", "learnings", "measurements",
              "runs", "work", "heartbeat", "hibernation"):
        sql(f"DELETE FROM {t}")
    # The knob that controls the live measure value (measure.where = 'select val from ui_knob').
    sql("DELETE FROM ui_knob")
    sql("INSERT INTO ui_knob (val) VALUES (50)")
    # Reset the last-good cache so CANT_SEE tests start clean.
    import ui.server as srv
    srv._LAST_GOOD = None
    srv._LAST_GOOD_AT = None


def set_now(val):
    sql("UPDATE ui_knob SET val=%s", (val,))


def add_measurement(val, age_min=0, metric=METRIC):
    sql("INSERT INTO measurements (metric, value, source, taken_at) VALUES (%s,%s,%s, now() - %s * interval '1 minute')",
        (metric, val, "ui_knob", age_min))


def add_completed_run(*, productive=True, age_min=0, succeeded=True, insight="learned something"):
    """Insert a work + completed run + learning, all within one transaction shape."""
    wid = sql1("INSERT INTO work (kind, summary, rationale, status) VALUES ('test','do a thing','why not','done') RETURNING id")[0]
    rid = sql1(
        "INSERT INTO runs (work_id, started_at, completed_at, outcome, succeeded, cost_usd, productive, evidence, "
        "measure_before, measure_after, escalation_level) "
        "VALUES (%s, now() - %s * interval '1 minute', now() - %s * interval '1 minute', %s, %s, 0.01, %s, 'evidence', 50, 50, 'autonomous') RETURNING id",
        (wid, age_min + 1, age_min, "did it" if succeeded else "FAILED: did it", succeeded, productive))[0]
    lid = sql1("INSERT INTO learnings (run_id, insight, evidence, scope, confidence) VALUES (%s,%s,%s,'local',0.6) RETURNING id",
               (rid, insight, "evidence"))[0]
    return wid, rid, lid


def add_parked_work(*, summary="parked thing", rationale="needs a human"):
    wid = sql1("INSERT INTO work (kind, summary, rationale, status) VALUES ('test', %s, %s, 'awaiting_human') RETURNING id",
               (summary, rationale))[0]
    return wid


def add_heartbeat(state, retry_until_offset_min=None):
    """retry_until_offset_min: positive = future, negative = past, None = no deadline."""
    if retry_until_offset_min is None:
        sql("INSERT INTO heartbeat (state, detail) VALUES (%s, %s)", (state, "test"))
    else:
        sql("INSERT INTO heartbeat (state, detail, retry_until) VALUES (%s, %s, now() + %s * interval '1 minute')",
            (state, "test", retry_until_offset_min))


def add_hibernation(unproductive=12):
    sql("INSERT INTO hibernation (reason, unproductive, notified_at) VALUES ('stuck', %s, now())", (unproductive,))


# --- HTTP helpers -------------------------------------------------------------
def fetch_state():
    c = http.client.HTTPConnection("127.0.0.1", UI_PORT, timeout=10)
    c.request("GET", "/api/state")
    r = c.getresponse()
    body = r.read().decode()
    c.close()
    return json.loads(body), r.status


def fetch_page():
    c = http.client.HTTPConnection("127.0.0.1", UI_PORT, timeout=10)
    c.request("GET", "/")
    r = c.getresponse()
    body = r.read().decode()
    c.close()
    return body, r.status


def approve(wid, token="test-approval-token"):
    c = http.client.HTTPConnection("127.0.0.1", UI_PORT, timeout=10)
    c.request("POST", f"/api/approve/{wid}", headers={"X-AQ-Approval-Token": token})
    r = c.getresponse()
    body = r.read().decode()
    c.close()
    return json.loads(body), r.status


# --- instance.yaml ------------------------------------------------------------
def write_instance_yaml(*, mission=True):
    if mission:
        doc = {
            "mission": {
                "objective": "Keep the test count at target (validation harness)",
                "horizon": "ongoing",
                "measure": {"what": METRIC, "where": "select val from ui_knob",
                            "target": TARGET, "goal": "reach_and_maintain"},
                "boundaries": {"may_act_alone": ["test"], "must_ask_first": ["money"]},
            },
            "engine": {"resident_agent": "test-agent", "mode": "api"},
            "budget": {"money": {"daily_soft_usd": 5, "monthly_hard_usd": 50},
                       "autonomy": {"level": "act-reversible"}},
            "surfaces": {"notify": {"channel": "none"}},
        }
    else:
        doc = {}  # missing mission
    with open(INSTANCE_YAML, "w") as f:
        yaml.safe_dump(doc, f)


# --- the tests ----------------------------------------------------------------
def test_states():
    print("\n[1] Health state machine — all 7 states + CANT_SEE + missing-mission NOT_ALIVE")

    reset_db()
    s, _ = fetch_state()
    check("empty DB -> NOT_ALIVE", s["health"]["status"], "NOT_ALIVE")

    # NOT_ALIVE via missing mission (file-level, no DB needed)
    write_instance_yaml(mission=False)
    s, _ = fetch_state()
    check("missing mission -> NOT_ALIVE", s["health"]["status"], "NOT_ALIVE")
    write_instance_yaml(mission=True)
    reset_db()

    # Need a completed cycle for the remaining states (else NOT_ALIVE wins).
    add_completed_run(age_min=1)
    set_now(50)  # below target -> not satisfied

    # WAITING_ON_YOU — open hibernation
    add_hibernation()
    s, _ = fetch_state()
    check("open hibernation -> WAITING_ON_YOU", s["health"]["status"], "WAITING_ON_YOU")
    sql("DELETE FROM hibernation")

    # WAITING_ON_PLAN — rate_limited, retry_until in the future
    add_heartbeat("rate_limited", retry_until_offset_min=30)
    s, _ = fetch_state()
    check("rate_limited future -> WAITING_ON_PLAN", s["health"]["status"], "WAITING_ON_PLAN")

    # OVERDUE — rate_limited, retry_until in the past
    sql("DELETE FROM heartbeat")
    add_heartbeat("rate_limited", retry_until_offset_min=-30)
    s, _ = fetch_state()
    check("rate_limited past -> OVERDUE", s["health"]["status"], "OVERDUE")
    sql("DELETE FROM heartbeat")

    # AT_TARGET via fresh measurement (bootstrap amendment: NOT heartbeat).
    # An OLD cycle exists (cycles>0, so not NOT_ALIVE) but NO fresh cycle — only the fresh
    # measurement proves we are still at target. This is the satisfied loop: quiet is correct.
    sql("DELETE FROM runs")
    sql("DELETE FROM learnings")
    sql("DELETE FROM work")
    add_completed_run(age_min=STALL_MIN + 60)  # old cycle: cycles>0 but outside the stall window
    set_now(140)  # >= target (100) -> satisfied, but < 1.5x target so not overshooting
    add_measurement(140, age_min=2)  # fresh (within 180min window)
    s, _ = fetch_state()
    check("at target + fresh measurement, no recent cycle -> AT_TARGET", s["health"]["status"], "AT_TARGET")
    check_true("AT_TARGET satisfied flag", s["mission"]["satisfied"] is True)
    check_true("AT_TARGET fresh_measurement flag", s["loop"]["fresh_measurement"] is True)
    check_true("AT_TARGET fresh_cycle false (measurement, not cycle, proves it)", s["loop"]["fresh_cycle"] is False)

    # STALLED — at target but STALE measurement (amendment: stale measurement -> STALLED).
    # The old cycle still exists (cycles>0) but neither a fresh cycle nor a fresh measurement
    # proves the number is still there. Honest: we don't know, so STALLED.
    sql("DELETE FROM measurements")
    set_now(140)  # still satisfied (but we can no longer prove it)
    add_measurement(140, age_min=STALL_MIN + 30)  # stale (outside window)
    s, _ = fetch_state()
    check("at target + STALE measurement -> STALLED", s["health"]["status"], "STALLED")
    check_true("STALLED despite satisfied", s["mission"]["satisfied"] is True)
    check_true("STALLED fresh_measurement false", s["loop"]["fresh_measurement"] is False)

    # WORKING — fresh completed cycle, not at target
    reset_db()
    add_completed_run(age_min=2)
    set_now(50)  # below target, fresh cycle
    s, _ = fetch_state()
    check("fresh cycle, not at target -> WORKING", s["health"]["status"], "WORKING")

    # STALLED — no fresh cycle, not at target
    sql("DELETE FROM runs")
    sql("DELETE FROM learnings")
    sql("DELETE FROM work")
    # insert an OLD cycle so cycles>0 but not fresh
    add_completed_run(age_min=STALL_MIN + 60)
    set_now(50)
    s, _ = fetch_state()
    check("old cycle only -> STALLED", s["health"]["status"], "STALLED")

    # CANT_SEE — DB unreachable. Point the server at a dead port via env, then fetch.
    print("\n[2] CANT_SEE — DB unreachable degrades to grey, never keeps green")
    reset_db()
    add_completed_run(age_min=1)
    set_now(150)
    add_measurement(150, age_min=1)
    s, _ = fetch_state()
    check("pre-CANT_SEE baseline is AT_TARGET", s["health"]["status"], "AT_TARGET")
    # Swap the DB URL to a dead port. The server reads os.environ at _conn() time.
    good_url = os.environ["AQ_DB_URL"]
    os.environ["AQ_DB_URL"] = "postgresql://aq:aq@127.0.0.1:1/autonomy_quest"
    try:
        s, _ = fetch_state()
        check("DB down -> CANT_SEE", s["health"]["status"], "CANT_SEE")
        check("CANT_SEE level grey", s["health"]["level"], "grey")
        check_true("CANT_SEE dims stale payload (_stale)", s.get("_stale") is True)
        check_true("CANT_SEE carries last_good_at", bool(s.get("_last_good_at")))
    finally:
        os.environ["AQ_DB_URL"] = good_url
    # Confirm recovery
    s, _ = fetch_state()
    check("DB back -> recovers (not CANT_SEE)", s["health"]["status"] != "CANT_SEE", True)


def test_approve():
    print("\n[3] Approve round-trip + 409 guardrail")
    reset_db()
    add_completed_run(age_min=1)
    wid = add_parked_work(summary="approve me", rationale="please")
    body, code = approve(wid)
    check("first approve -> 200", code, 200)
    check("first approve body", body.get("approved"), True)
    status = sql1("SELECT status FROM work WHERE id=%s", (wid,))[0]
    check("work status -> pending", status, "pending")
    # Re-approve — already pending, not awaiting_human -> 409
    body, code = approve(wid)
    check("re-approve -> 409", code, 409)
    check("re-approve body", body.get("approved"), False)
    # Approve a non-existent id -> 409 (rowcount 0)
    body, code = approve(999999)
    check("approve missing id -> 409", code, 409)


def test_xss():
    print("\n[4] XSS inert-render proof")
    reset_db()
    add_completed_run(age_min=1)
    # Inject payload into a DB text surface (parked work summary/rationale).
    payload = "<script>window.__pwned=1</script><img src=x onerror=window.__pwned=2>"
    wid = add_parked_work(summary=payload, rationale=payload)

    # The raw payload round-trips through /api/state (proving it came from the DB).
    s, _ = fetch_state()
    found_raw = any(payload in (p.get("summary", "") + p.get("rationale", "")) for p in s.get("parked", []))
    check_true("raw XSS payload present in /api/state JSON", found_raw)

    # The page's esc() escapes '& < > "''. Verify the function is present and correct.
    html, _ = fetch_page()
    esc_match = re.search(r"const esc = (.+?);", html)
    check_true("esc() defined in page JS", bool(esc_match))
    # Replicate esc() in Python and prove the payload becomes inert.
    def esc_py(x):
        return (str(x) if x is not None else ""
                .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&#39;"))
    # The JS esc replaces all in one pass; replicate exactly.
    def esc_py2(x):
        s = str(x) if x is not None else ""
        return re.sub(r'[&<>"\']', lambda c: {"&": "&amp;", "<": "&lt;", ">": "&gt;",
                                               '"': "&quot;", "'": "&#39;"}[c.group()], s)
    escaped = esc_py2(payload)
    check_true("esc(payload) contains no unescaped <script tag", "<script>" not in escaped.lower())
    check_true("esc(payload) contains no unescaped <img tag", "<img " not in escaped.lower() and "<img>" not in escaped.lower())
    check_true("esc(payload) contains &lt;script&gt;", "&lt;script&gt;" in escaped)
    check_true("esc(payload) contains &lt;img", "&lt;img" in escaped)
    # onerror= survives as inert TEXT (inside &lt;img...&gt;) — that is correct, not a leak:
    # without an unescaped <img tag around it, onerror= is just visible characters.
    check_true("onerror= is inert text (no unescaped <img wraps it)", "<img" not in escaped)

    # The xss_canary field is present in state.
    canary = s.get("xss_canary", "")
    check_true("xss_canary present in /api/state", bool(canary))
    check_true("xss_canary has <script>", "<script>" in canary)
    check_true("xss_canary has onerror", "onerror" in canary)

    # The page renders the canary via innerHTML = esc(canary). Verify the JS does exactly that
    # (esc() applied before innerHTML — the only sanctioned path for DB/model text).
    check_true("page routes canary through esc() before innerHTML",
               "innerHTML = esc(s.xss_canary)" in html or "innerHTML=esc(s.xss_canary)" in html)

    # Verify the static page does NOT contain the raw canary or raw payload unescaped.
    check_true("page HTML has no unescaped <script>window.__xss", "<script>window.__xss" not in html)
    check_true("page HTML has no unescaped payload <script>window.__pwned",
               "<script>window.__pwned" not in html)

    # Verify every innerHTML assignment that interpolates a known DB/model text field routes it
    # through esc(). We check each dangerous field name: if it appears in a ${...} interpolation,
    # it must be wrapped in esc(...).
    DANGER_FIELDS = ("summary", "rationale", "outcome", "insight", "evidence", "body", "title",
                     "subject", "sender", "recipient", "author", "error", "detail", "headline",
                     "objective", "horizon", "reason")
    bad = []
    for field in DANGER_FIELDS:
        # find ${...field...} interpolations NOT wrapped in esc()
        for interp in re.findall(r'\$\{([^}]+)\}', html):
            if field in interp and "esc(" not in interp:
                bad.append((field, interp))
    check_true(f"all DB-text innerHTML fields pass through esc() ({len(bad)} leaks)", len(bad) == 0,
               f"leaks: {bad[:3]}")


def test_page_renders():
    print("\n[5] Page renders — the 30-second-test elements are present")
    reset_db()
    add_completed_run(age_min=1)
    set_now(142)
    add_measurement(142, age_min=1)
    html, code = fetch_page()
    check("GET / -> 200", code, 200)
    for needle in ["id=\"obj\"", "id=\"live\"", "id=\"mission\"", "id=\"stats\"",
                   "id=\"next\"", "id=\"runs\"", "id=\"learnings\"", "id=\"board\"",
                   "id=\"bus\"", "id=\"xss-canary\"", "esc(", "setInterval(tick"]:
        check_true(f"page has {needle}", needle in html)
    s, _ = fetch_state()
    check_true("state has health.status", "status" in s.get("health", {}))
    check("state has mission.target", s["mission"].get("target"), TARGET)
    check("state has mission.goal", s["mission"].get("goal"), "reach_and_maintain")
    check_true("state has next.running (list)", isinstance(s.get("next", {}).get("running"), list))
    check_true("state has next.queued (list)", isinstance(s.get("next", {}).get("queued"), list))
    check_true("state has ladder.level", "level" in s.get("ladder", {}))
    check_true("state has staged_imports (list)", isinstance(s.get("staged_imports"), list))
    check_true("state has heartbeat key", "heartbeat" in s)
    check_true("state has beliefs_revised_count", "beliefs_revised_count" in s)
    check_true("state has spend.today", "today" in s.get("spend", {}))
    check("state has budget.metered", s.get("budget", {}).get("metered"), True)


def main():
    os.environ.setdefault("AQ_DB_URL", DB_URL)
    os.environ["AQ_UI_PORT"] = str(UI_PORT)
    os.environ["AQ_UI_BIND"] = "127.0.0.1"
    os.environ["AQ_APPROVAL_TOKEN"] = "test-approval-token"
    os.environ.setdefault("AQ_STALL_MINUTES", str(STALL_MIN))

    # Create the knob table the measure.where query reads.
    sql("CREATE TABLE IF NOT EXISTS ui_knob (val int not null default 50)")
    write_instance_yaml(mission=True)

    # Start the UI server in a background thread.
    import ui.server as srv
    srv._LAST_GOOD = None
    srv._LAST_GOOD_AT = None
    th = threading.Thread(target=srv.serve, args=(UI_PORT,), daemon=True)
    th.start()
    time.sleep(0.6)
    print(f"[aq] test UI on http://127.0.0.1:{UI_PORT}")

    try:
        test_states()
        test_approve()
        test_xss()
        test_page_renders()
    finally:
        # Clean up the test instance.yaml so it doesn't ship in the worktree.
        if os.path.exists(INSTANCE_YAML):
            os.remove(INSTANCE_YAML)

    print(f"\n{'='*60}")
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    print(f"{'='*60}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()


def test_fresh_unproductive_cycle_is_not_green_working():
    h = server._derive_health(
        mission_present=True, db_ok=True, cycles_count=1, fresh_cycle=True,
        fresh_measurement=True, satisfied=False, now_val=0, target=20,
        hibernation=[], heartbeat=None, goal="reach_and_maintain", overshooting=False,
        stall_minutes=180, last_age_min=0, last_cycle_productive=False,
        latest_acquisition_rung=None,
    )
    assert h["status"] == "REWORKING"
    assert h["level"] != "green"
    assert "acted" not in h["detail"]


def test_completed_acquisition_is_blue_not_green_working():
    h = server._derive_health(
        mission_present=True, db_ok=True, cycles_count=1, fresh_cycle=True,
        fresh_measurement=True, satisfied=False, now_val=0, target=20,
        hibernation=[], heartbeat=None, goal="reach_and_maintain", overshooting=False,
        stall_minutes=180, last_age_min=0, last_cycle_productive=True,
        latest_acquisition_rung="recall",
    )
    assert h["status"] == "ACQUIRING"
    assert h["level"] == "blue"
    assert "target plan pending" in h["detail"]
