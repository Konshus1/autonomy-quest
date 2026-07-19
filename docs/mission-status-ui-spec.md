# autonomy-quest Mission Status UI — Design Spec
*Authored by a Claude Code session with model = Fable (claude-fable-5), per Kevin. For build by the hermes/kimi-k3 window (task #3023).*
*Repo: github.com/Konshus1/autonomy-quest — read-only clone: /Users/kevincthomas/src/autonomy-quest-public-19ea8b6*
*Baseline: EXTEND the existing `ui/server.py` ("THE BOARD"); do not rewrite from scratch.*

---

## 1. Executive Summary
- **Extend, don't replace**: `ui/server.py` already serves a doctrine-correct single-page BOARD (stdlib HTTP server + one embedded HTML page + `/api/state` JSON polled every 5s). Add the missing panels — mission target/goal & trend, the cycle story, "up next" queue, budget-vs-caps, heartbeat-aware health states — and a newcomer legend layer.
- **One health banner, seven states**: `CAN'T SEE IT` / `NOT ALIVE` / `WAITING ON YOU` / `WAITING FOR PLAN RESET` / `AT TARGET — HOLDING` / `WORKING` / `STALLED`, derived from ground truth (`runs JOIN learnings`, `heartbeat`, `hibernation`) — never from "the process is up."
- **The mission is a progress bar with a ceiling**: show `now / target`, the `reach_and_maintain` vs `maximize` goal in plain words, and render the "satisfied loop" (at target, quiet, not broken) as a *good* state.
- **Cycles become stories**: each recent run renders as observe → decide → act → record → learn built from `work` + `runs` + `learnings` columns, plain language, not raw rows.
- **Fails honest**: every state the UI can't verify is shown as "can't verify," never green. DB down ≠ "no problems found."
- **Stays one-file, zero-build**: stdlib + psycopg2 + PyYAML only (already required); ship in container via `./aq.py ui` + `EXPOSE 8080`.

## 2. What Exists Today
`ui/server.py` (~372 lines): `GET /` one embedded HTML page (light/dark, no JS deps, 5s poll); `GET /api/state` single JSON; `POST /api/approve/{work_id}` (only write: `work.status awaiting_human→pending`). `state()` already reads instance.yaml mission, live measure value (re-executed, never cached), loop-turning check (`runs JOIN learnings` within AQ_STALL_MINUTES=180), hibernation, month spend, parked work, last 20 runs+learnings, learnings, last 40 measurements (fetched but NEVER rendered — dangling wire), bb_notes board, bb_messages bus.
Gaps to close: no target/goal/satisfied display; trend fetched-not-rendered; no up-next queue; spend without caps; heartbeat unused (rate-limited shows as STALLED); no per-cycle story; no ladder position; no legend; not in container.

## 3. Purpose & Audience — the 30-Second Test
Primary: first-time, possibly non-technical viewer. Within 30s with zero training they must answer: (1) What is it trying to do? [objective as H1] (2) Is it working right now? [one banner, one color, one sentence — never two disagreeing signals] (3) How close to done? [number vs target + bar] (4) Does it need me? [loud when non-empty, absent when empty] (5) What did it just do? [last cycle as one-line story]. Everything else = depth on scroll. Anti-goals: not an admin console/log viewer/config editor; only write remains approving parked work.

## 4. Information Architecture (one column, ~980px)
1 HEADER (objective H1 + identity line) · 2 HEALTH BANNER (the answer to "is it working?") · 3 MISSION PANEL (now vs target, goal, sparkline) · 4 WAITING ON YOU (parked + hibernation; hidden when empty) · 5 VITALS STRIP (cycles · last cycle · spend/caps · engine · autonomy) · 6 UP NEXT (running + queued) · 7 THE LOOP (cycles as observe→decide→act→record→learn stories) · 8 WHAT IT BELIEVES (learnings + staged imports) · 9 THE BOARD (bb_notes) · 10 THE BUS (bb_messages, undelivered first) · 11 LEGEND FOOTER. Sections 4 and staged-imports are conditionally rendered (absence shown as absence, not empty box).

## 5. The Health Banner (server-side state machine, first match wins)
0 CANT_SEE — DB/state() raised — grey — "Can't see the system…including whether things are fine."
1 NOT_ALIVE — completed `runs JOIN learnings` == 0 — amber — "Not alive yet. Installed is not alive."
2 WAITING_ON_YOU — open hibernation (resumed_at IS NULL) — amber ✋ — "Paused — waiting on you… resumes when you approve, not on a timer."
3 WAITING_ON_PLAN — latest heartbeat state=rate_limited AND retry_until>now() — blue — "Waiting for its plan to reset… retries by {retry_until}."
4 OVERDUE — heartbeat rate_limited AND retry_until<=now() — amber — "Overdue… deadline passed. Something is wrong."
5 AT_TARGET — goal=reach_and_maintain AND now>=target AND (fresh cycle OR fresh heartbeat ≤ stall window) — green — "At target — holding ({now} of {target}). Quiet is correct here."
6 WORKING — fresh completed run JOIN learning within AQ_STALL_MINUTES — green — "Working. Last full cycle {ago}."
7 STALLED — none of the above — red/amber — "Stalled. No cycle finished in {stall} min."
Notes: state 5 before 7 = the satisfied-loop fix (fresh heartbeat keeps an at-target instance green; at-target AND stale heartbeat still falls to STALLED — honest). State 2 uses distinct ✋ (not ⚠/✓). `overshooting` (now>1.5×target) has no own state — loop parks itself awaiting_human on overshoot → surfaces as state 2 with rationale; mission bar paints overflow amber (§6.2).

## 6. Panel Specs & Data Mapping
6.1 Header: H1 = mission.objective. Identity line (mono): resident_agent · mode · autonomy-in-plain-words · horizon.
6.2 Mission Panel: name=measure.what; current=live exec of measure.where (never cached; on fail show error); target=measure.target OR live measure.target_query (query wins, resolved live); goal=measure.goal; sparkline=measurements(taken_at,value) last 40 (render inline SVG polyline ~120×28, no lib); caption=measurements.source latest. Render big number "142 of 200" + bar. reach_and_maintain: end-cap "target — then hold"; at target → full/green "at target — maintaining"; >1.5×target → amber overflow "past target — being checked". maximize: no cap, "more is the point", show trend+best-ever. target null on reach_and_maintain → amber "No target set — verify.sh fails measures without a ceiling."
6.3 Waiting On You: parked = work status=awaiting_human (id,kind,summary,rationale,created_at); why-parked line from autonomy level; age emphasis (>24h amber border); hibernation context (hibernated_at,reason,unproductive,notified_at); approve button = POST /api/approve/{id} ("Approve — let it proceed").
6.4 Vitals Strip: cycles=count(runs JOIN learnings completed); last cycle=max(runs.completed_at) joined learnings; spend today vs daily_soft_usd (mini-bar); month spend vs monthly_hard_usd (≥80% amber, at cap red); subscription mode (monthly_hard==0) → "flat-rate plan — limited by usage resets, not dollars" (never "$0 of $0"); unproductive streak + ladder (recompute like escalation.unproductive_streak(); 5 dots autonomous→self-nudge→peer→human→hibernate from runs.escalation_level).
6.5 Up Next: running = work status=running + open runs row; queued = work status=pending ORDER BY approved_at DESC NULLS LAST, created_at LIMIT 5; empty+at-target = "Nothing queued — at target and holding"; empty+not = "next cycle decides when it runs".
6.6 The Loop (cycle strip per run): last 10 completed runs r JOIN work w LEFT JOIN learnings l + r.productive,evidence,measure_before/after,error,rolled_back,escalation_level,started_at. Phases: observed(measure_before) · decided(work.kind/summary/rationale) · acted(succeeded/error/rolled_back/duration/cost) · recorded(outcome/productive/evidence/measure_after; "produced something real" vs amber "nothing real produced") · learned(insight/evidence/scope/confidence; missing learning on completed run = amber "no learning recorded"). Orphaned runs (outcome LIKE 'ORPHANED:%') = "interrupted mid-cycle — side effects unknown".
6.7 What It Believes: learnings superseded_by IS NULL LIMIT 20 (insight/evidence/scope/confidence); confidence+scope per §7 vocab; "…and N earlier beliefs revised"; staged imports strip (shared_learnings status=staged) — INERT, review via `./aq.py share review`, READ-ONLY (no one-click promote).
6.8 Board & Bus: keep; bus third state handled_at→"acted on"; board kind chips tooltips.
6.9 Legend Footer: <details> "How to read this page" — §7 vocab table + 7 banner states + loop diagram.

## 7. Clarity-for-Newcomers
Principles: one idea/element; sentences over tables; states explained where they appear; numbers get comparators ("of {target}", "{ago}"); empty states teach; native title + <details> tooltips only; "quiet is correct" stated in-place. Vocabulary (term → label → explainer): cycle→"a full loop"; measure→"the mission's number"; reach_and_maintain→"reach it, then hold it"; maximize→"more is the point"; satisfied→"at target — holding"; stalled→"stalled"; hibernating→"paused, waiting on you"; rate-limited→"waiting for its plan to reset"; parked→"asked first"; productive→"produced something real"; scope local→"applies here"; scope generalisable→"could help other missions"; confidence→hunch(<0.4)/probably(0.4–0.7)/confident(>0.7); soft cap→"slow-down line"; hard cap→"stop line"; ladder→"how it asks for help (stages)". Autonomy levels: propose→"asks before everything"; act-reversible→"acts alone only when undoable"; act-external→"acts alone except money & code"; act-broad→"acts alone inside its boundaries".

## 8. Honesty Guardrails
1 Liveness=the row not the process (computed in DB vs now(), never client clock/process-up/cached). 2 Fail visible not blank: state()/fetch error → CAN'T_SEE grey + dim stale panels + "last good data: {ago}" (keep prior payload only to DIM it). 3 Measure never remembered (re-exec per request; on fail show error not stale). 4 No vacuous all-clears (always show when evidence is from). 5 Hibernation never dressed as failure/success (✋ amber + resume path). 6 rate_limited degrades to OVERDUE past retry_until. 7 Server-side state derivation (page renders status verbatim). 8 One write stays one write (approve keeps awaiting_human guard; 409 "changed underneath you"). 9 No auth theater: footer "local window — anyone who can reach this port sees everything"; default bind 127.0.0.1 with AQ_UI_BIND override; no login system.

## 9. Tech Constraints
Keep architecture exactly: one file, stdlib http.server, psycopg2+PyYAML (already required), one embedded HTML, one JSON endpoint, 5s poll. NO FastAPI/node/build/CDN/websockets/chart-lib. Sparkline = inline svg polyline (~15 lines JS). /api/state additions: mission.{target,goal,satisfied,overshooting}, health.{status,headline,detail}, spend.{today,daily_soft,monthly_hard,metered}, heartbeat latest, next.{running,queued}, ladder.{streak,level}, extended runs cols, staged_imports, beliefs_revised_count. Container: install ui/, EXPOSE 8080, run UI alongside loop from entrypoint (UI crash never stops loop). Launch: ./aq.py ui, AQ_UI_PORT default 8080, DB via .env.

## 10. Schema Gaps (none block phase 1)
1 No observe snapshot → approximate via runs.measure_before/after (nullable → "—"); opt migration runs.observed_summary. 2 No queue priority on work → arrival order ("queued" not "next"). 3 Ladder streak derived not stored (recompute; comment → escalation.py). 4 Caps in instance.yaml not DB (file is ground truth). 5 hibernation.resume_signal only at resume (hardcode explainer). 6 No UI/auth token (guardrail 9).

## 11. Wireframe
(one column, top→bottom): HEADER objective H1 + identity line · HEALTH BANNER (● WORKING — last full cycle 12m ago…) · THE MISSION'S NUMBER (142 of 200, bar with "target — then hold ▲" cap, sparkline) · ✋ WAITING ON YOU (parked card + why + age + [Approve]) · VITALS (87 loops · 12m ago · $1.20 of $5 · $14 of $50 · ●○○○○ ladder) · UP NEXT (Now doing… / Queued…) · THE LOOP (per-cycle: observed/decided/acted/recorded/learned) · WHAT IT BELIEVES (learnings + "2 ideas arrived — inert until ./aq.py share review") · THE BOARD · THE BUS (UNREAD first) · ▸ How to read this page (legend) + "local window" footer.

## 12. Out of Scope / Phase 2
Any write beyond approve; auth/multi-user/remote hardening; history drill-downs/pagination/AGE graph viz; live push (SSE/ws); runs.observed_summary migration; multi-instance fleet view; UI-originated notifications.

## 13. Acceptance Criteria
1 ./aq.py ui serves full page, stdlib+psycopg2+PyYAML only. 2 All seven banner states reachable + visually verified by FORCING each (empty DB→NOT_ALIVE; insert hibernation→WAITING_ON_YOU; heartbeat rate_limited future/past→3/4; stop DB→CAN'T_SEE). 3 DB stopped mid-session → page degrades within 30s (dim + grey), never keeps green. 4 reach_and_maintain at target shows green "at target — holding" not STALLED while heartbeat fresh; STALLED once heartbeat stale. 5 sparkline renders (dangling trend consumed). 6 approve round-trips + 409 treatment. 7 container includes UI, EXPOSE 8080, UI crash doesn't stop loop. 8 a non-technical reader answers the five §3 questions from the screenshot (test on a human).

Builder read-first: ui/server.py, aq.py, schema/001–008, runner/loop.py (world() ~L279 satisfied/overshoot/target_query), runner/budget.py (metered $0-cap trap), runner/escalation.py (ladder+streak), docs/what-this-is.md + doctrine.md, container/Dockerfile L6.
