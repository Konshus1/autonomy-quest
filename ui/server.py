"""The window into a running instance.

Deliberately stdlib-only — no FastAPI, no node, no build step. This whole system is supposed to
be handable to someone who has never seen it, and every dependency is a way for that to fail on
their machine.

What it shows, in priority order:

  1. IS THE LOOP TURNING?  Not "is the process up" — when did a cycle last complete a full
     acted→recorded→LEARNED pass? A stalled loop behind a healthy-looking process is the failure
     this whole system exists to make impossible, so it is the first thing on the page.
  2. Is the mission's number moving?
  3. What is it waiting on YOU for? A parked decision nobody looks at is a dead instance.
  4. What has it learned? This is the part that makes it more than a cron job.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg2
import psycopg2.extras
import yaml

STALL_MINUTES = int(os.environ.get("AQ_STALL_MINUTES", "180"))


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


def state() -> dict:
    inst = yaml.safe_load(open("instance.yaml")) or {}
    mission = inst.get("mission") or {}
    measure = mission.get("measure") or {}

    # THE number, re-read from its real source. Not cached, not remembered.
    try:
        now_val = _rows(measure["where"])[0]
        now_val = float(list(now_val.values())[0])
        measure_error = None
    except Exception as e:
        now_val, measure_error = None, str(e).strip().split("\n")[0]

    # IS THE LOOP TURNING? Ground truth: a completed run WITH a learning, recently.
    turning = _rows(
        """select count(*) as n, max(r.completed_at) as last
           from runs r join learnings l on l.run_id = r.id
           where r.completed_at is not null""")[0]
    fresh = _rows(
        """select count(*) as n from runs r join learnings l on l.run_id = r.id
           where r.completed_at > now() - interval '%s minutes'""" % STALL_MINUTES)[0]

    return {
        "mission": {
            "objective": mission.get("objective", ""),
            "measure": measure.get("what", ""),
            "now": now_val,
            "error": measure_error,
            "horizon": mission.get("horizon", ""),
        },
        "loop": {
            "cycles": turning["n"],
            "last_cycle": turning["last"].isoformat() if turning["last"] else None,
            # A loop that has not completed a cycle in STALL_MINUTES is STALLED, however healthy
            # the process looks. This is the liveness assertion, on the page.
            "turning": fresh["n"] > 0,
            "stall_minutes": STALL_MINUTES,
        },
        "engine": {
            "mode": (inst.get("engine") or {}).get("mode", "?"),
            "agent": (inst.get("engine") or {}).get("resident_agent", "?"),
            "autonomy": ((inst.get("budget") or {}).get("autonomy") or {}).get("level", "?"),
        },
        "spend": _rows(
            """select coalesce(sum(cost_usd),0) as month,
                      coalesce(sum(tokens_in+tokens_out),0) as tokens
               from runs where started_at >= date_trunc('month', now())""")[0],
        "parked": _rows(
            "select id, kind, summary, rationale, created_at from work "
            "where status='awaiting_human' order by created_at"),
        "runs": _rows(
            """select r.id, r.succeeded, r.completed_at, r.outcome, r.cost_usd,
                      w.kind, w.summary, l.insight, l.scope, l.confidence
               from runs r
               join work w on w.id = r.work_id
               left join learnings l on l.run_id = r.id
               where r.completed_at is not null
               order by r.completed_at desc limit 20"""),
        "learnings": _rows(
            "select insight, evidence, scope, confidence, created_at from learnings "
            "where superseded_by is null order by created_at desc limit 20"),
        "trend": _rows(
            "select taken_at, value from measurements order by taken_at desc limit 40"),
    }


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
            try:
                self._send(200, json.dumps(state(), default=_json_safe))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
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
                wid = int(self.path.rsplit("/", 1)[1])
                with _conn() as c, c.cursor() as cur:
                    cur.execute(
                        "UPDATE work SET status='pending', approved_at=now() "
                        "WHERE id=%s AND status='awaiting_human'", (wid,))
                    ok = cur.rowcount == 1
                self._send(200 if ok else 409,
                           json.dumps({"approved": ok, "id": wid}))
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
    --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    --body:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
    --display:"Iowan Old Style",Charter,Georgia,serif;
  }
  @media (prefers-color-scheme:dark){:root{
    --paper:#0C1512; --paper-2:#131F1A; --ink:#DEE7E2; --soft:#9AAAA3; --faint:#67776F;
    --rule:#26332E; --accent:#4FD1A5; --accent-w:#10241C; --warn:#E08843; --warn-w:#2A1B0F;}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--body);line-height:1.6}
  .wrap{max-width:980px;margin:0 auto;padding:2.5rem 1.25rem 5rem}
  h1{font-family:var(--display);font-size:1.5rem;margin:0;letter-spacing:-.01em}
  .sub{font-family:var(--mono);font-size:.7rem;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);margin-bottom:.4rem}
  section{border-top:1px solid var(--rule);padding:1.75rem 0}
  h2{font-family:var(--display);font-size:1.05rem;margin:0 0 1rem}
  .eyebrow{font-family:var(--mono);font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin-bottom:.5rem}

  /* the liveness banner — the most important thing on the page */
  .live{display:flex;align-items:center;gap:.8rem;padding:1rem 1.2rem;border:1px solid var(--rule);
        background:var(--paper-2);border-left:4px solid var(--accent);margin:1.25rem 0}
  .live.stalled{border-left-color:var(--warn);background:var(--warn-w)}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--accent);flex:none}
  .live.stalled .dot{background:var(--warn)}
  .live b{font-weight:600}
  .live .why{color:var(--soft);font-size:.9rem}

  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--rule);border:1px solid var(--rule)}
  .cell{background:var(--paper);padding:.9rem 1rem}
  .k{font-family:var(--mono);font-size:.63rem;letter-spacing:.1em;text-transform:uppercase;color:var(--faint)}
  .v{font-family:var(--display);font-size:1.55rem;font-variant-numeric:tabular-nums;margin-top:.15rem}
  .v small{font-size:.8rem;color:var(--faint)}

  .park{border:1px solid var(--warn);background:var(--warn-w);padding:1rem 1.2rem;margin-bottom:.75rem}
  .park h3{margin:0 0 .3rem;font-size:.98rem;font-family:var(--display)}
  .park p{margin:.3rem 0 .8rem;color:var(--soft);font-size:.9rem}
  button{font-family:var(--body);font-size:.85rem;font-weight:600;padding:.45rem 1rem;border:1px solid var(--accent);
         background:var(--accent);color:var(--paper);cursor:pointer}
  button:hover{opacity:.88} button:focus-visible{outline:2px solid var(--ink);outline-offset:2px}

  .row{border-bottom:1px solid var(--rule);padding:.85rem 0;display:grid;grid-template-columns:2.6rem 1fr;gap:.9rem}
  .row:last-child{border-bottom:0}
  .id{font-family:var(--mono);font-size:.78rem;color:var(--faint);padding-top:.15rem}
  .sm{font-size:.9rem}
  .out{color:var(--soft);font-size:.86rem;margin-top:.2rem}
  .learn{margin-top:.45rem;padding-left:.7rem;border-left:2px solid var(--accent);font-size:.86rem}
  .learn em{color:var(--faint);font-style:normal;font-family:var(--mono);font-size:.7rem;letter-spacing:.06em;text-transform:uppercase}
  .tag{font-family:var(--mono);font-size:.62rem;letter-spacing:.08em;text-transform:uppercase;padding:.1rem .4rem;border:1px solid currentColor}
  .ok{color:var(--accent)} .bad{color:var(--warn)}
  .empty{color:var(--faint);font-size:.9rem}
</style></head><body>
<div class="wrap">
  <div class="sub">autonomy-quest</div>
  <h1 id="obj">…</h1>

  <div class="live" id="live"><span class="dot"></span><span id="livetext"></span></div>

  <div class="grid" id="stats"></div>

  <section id="parksec" hidden>
    <div class="eyebrow">Waiting on you</div>
    <h2>It stopped and asked, rather than acting</h2>
    <div id="parked"></div>
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
  </section>
</div>
<script>
const esc = s => String(s??"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const ago = iso => {
  if(!iso) return "never";
  const m = Math.floor((Date.now() - new Date(iso))/60000);
  if (m < 1) return "just now";
  if (m < 60) return m + "m ago";
  const h = Math.floor(m/60); if (h < 24) return h + "h ago";
  return Math.floor(h/24) + "d ago";
};

async function tick(){
  const s = await (await fetch("/api/state")).json();
  if (s.error){ document.getElementById("livetext").textContent = "cannot read the instance: " + s.error; return; }

  document.getElementById("obj").textContent = s.mission.objective || "(no mission — the interview was skipped)";

  // LIVENESS FIRST. "Installed" is not "done"; a turning loop is.
  const live = document.getElementById("live"), t = document.getElementById("livetext");
  if (s.loop.cycles === 0){
    live.className = "live stalled";
    t.innerHTML = "<b>NOT ALIVE.</b> <span class='why'>The loop has never completed a full cycle. Installed is not done.</span>";
  } else if (!s.loop.turning){
    live.className = "live stalled";
    t.innerHTML = "<b>STALLED.</b> <span class='why'>No cycle completed in "+s.loop.stall_minutes+" minutes — last was "+ago(s.loop.last_cycle)+". The processes may be up; the loop is not turning.</span>";
  } else {
    live.className = "live";
    t.innerHTML = "<b>Turning.</b> <span class='why'>Last cycle "+ago(s.loop.last_cycle)+" — acted, recorded, and learned.</span>";
  }

  const spend = s.engine.mode === "subscription"
    ? '<div class="v">$0 <small>subscription</small></div>'
    : '<div class="v">$'+Number(s.spend.month).toFixed(2)+' <small>this month</small></div>';
  document.getElementById("stats").innerHTML = `
    <div class="cell"><div class="k">${esc(s.mission.measure)||"measure"}</div>
      <div class="v">${s.mission.now ?? "—"}</div></div>
    <div class="cell"><div class="k">cycles</div><div class="v">${s.loop.cycles}</div></div>
    <div class="cell"><div class="k">spend</div>${spend}</div>
    <div class="cell"><div class="k">engine</div><div class="v" style="font-size:1rem">${esc(s.engine.agent)}<br><small>${esc(s.engine.autonomy)}</small></div></div>`;

  // Parked work — a decision nobody looks at is a dead instance.
  const ps = document.getElementById("parksec"), pd = document.getElementById("parked");
  ps.hidden = !s.parked.length;
  pd.innerHTML = s.parked.map(w => `
    <div class="park"><h3>${esc(w.summary)}</h3><p>${esc(w.rationale)}</p>
      <button onclick="approve(${w.id})">Approve — let it proceed</button></div>`).join("");

  document.getElementById("runs").innerHTML = s.runs.length ? s.runs.map(r => `
    <div class="row"><div class="id">#${r.id}</div><div>
      <div class="sm"><span class="tag ${r.succeeded?'ok':'bad'}">${r.succeeded?'done':'failed'}</span>
        &nbsp;${esc(r.summary)}</div>
      <div class="out">${esc(r.outcome)}</div>
      ${r.insight ? `<div class="learn"><em>learned · ${esc(r.scope)} · ${r.confidence}</em><br>${esc(r.insight)}</div>` : ''}
    </div></div>`).join("") : '<div class="empty">No cycles yet.</div>';

  document.getElementById("learnings").innerHTML = s.learnings.length ? s.learnings.map(l => `
    <div class="row"><div class="id">${l.scope==='generalisable'?'↗':'·'}</div><div>
      <div class="sm">${esc(l.insight)}</div>
      <div class="out">${esc(l.evidence)}</div></div></div>`).join("")
    : '<div class="empty">Nothing learned yet — that is what the first cycle is for.</div>';
}
async function approve(id){
  await fetch("/api/approve/"+id, {method:"POST"});
  tick();
}
tick(); setInterval(tick, 5000);
</script></body></html>"""


def serve(port: int = 8080) -> None:
    print(f"[aq] UI on http://localhost:{port}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    serve(int(os.environ.get("AQ_UI_PORT", "8080")))
