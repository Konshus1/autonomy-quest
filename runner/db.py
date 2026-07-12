"""The durable history. Everything the system knows, and the only thing it trusts.

Design rule running through this file: the database is GROUND TRUTH and memory is not.
Costs are summed from rows, the mission's number is re-read from its real source, and the
record+learn write is one transaction. Nothing important is held in a process that a crash
can reset.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from decimal import Decimal

import psycopg2
import psycopg2.extras

log = logging.getLogger("aq.db")


class PromotionRefused(RuntimeError):
    """A foreign learning was promoted on prose alone. A reason explains; it does not authorise."""


class MeasureUnreadable(RuntimeError):
    """The mission's number could not be read from its real source.

    We STOP rather than proceed on a stale or assumed value. A loop steering on a number it
    couldn't actually read is worse than a halted loop, because it looks like it's working.
    """


@dataclass
class Work:
    id: int
    kind: str
    summary: str
    rationale: str
    requires_human: bool = False
    reversible: bool = True
    spends_money: bool = False
    touches_human: bool = False
    commits: bool = False


class Db:
    def __init__(self, url: str, graph: str = "age") -> None:
        self.url = url
        self.graph = graph
        self.conn = psycopg2.connect(url)
        self.conn.autocommit = True

    @contextlib.contextmanager
    def tx(self):
        """One transaction. Used to make record+learn atomic — see loop.py invariant 1."""
        self.conn.autocommit = False
        try:
            with self.conn.cursor() as cur:
                yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self.conn.autocommit = True

    def _q(self, sql: str, args=(), one=False):
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, args)
            if cur.description is None:
                return None
            return cur.fetchone() if one else cur.fetchall()

    # -- observe: GROUND TRUTH ------------------------------------------------
    def read_measure(self, measure) -> Decimal:
        """Re-read the mission's number from wherever it actually lives.

        `measure.where` is the query the human gave us in the interview. We run THAT.
        We do not read a cached copy, and we do not ask the model how it thinks it's going.
        """
        try:
            row = self._q(measure.where, one=True)
        except Exception as e:
            raise MeasureUnreadable(
                f"could not read {measure.what!r} from its source: {e}. "
                f"Stopping rather than steering on a number we did not actually read."
            ) from e
        if not row:
            raise MeasureUnreadable(f"{measure.what!r} query returned no rows: {measure.where!r}")
        return Decimal(str(list(row.values())[0]))

    def record_measurement(self, measure, value: Decimal) -> None:
        self._q(
            "INSERT INTO measurements (metric, value, source) VALUES (%s, %s, %s)",
            (measure.what, value, measure.where),
        )

    def measure_trend(self, measure, days: int = 14):
        return self._q(
            "SELECT taken_at, value FROM measurements WHERE metric=%s "
            "AND taken_at > now() - (%s || ' days')::interval ORDER BY taken_at",
            (measure.what, days),
        )

    def live_learnings(self, limit: int = 50):
        """What it believes right now. Superseded beliefs are excluded, not deleted —
        this is what makes cycle 100 different from cycle 1."""
        return self._q(
            "SELECT id, insight, evidence, scope, confidence FROM learnings "
            "WHERE superseded_by IS NULL ORDER BY confidence DESC, created_at DESC LIMIT %s",
            (limit,),
        )

    def open_work(self):
        return self._q("SELECT * FROM work WHERE status IN ('pending','running') ORDER BY created_at")

    def awaiting_human(self):
        return self._q("SELECT * FROM work WHERE status='awaiting_human' ORDER BY created_at")

    def recent_runs(self, limit: int = 10):
        return self._q(
            "SELECT r.id, r.outcome, r.succeeded, r.rolled_back, w.kind, w.summary "
            "FROM runs r JOIN work w ON w.id=r.work_id "
            "WHERE r.completed_at IS NOT NULL ORDER BY r.completed_at DESC LIMIT %s",
            (limit,),
        )

    # -- budget: summed from ROWS, never from a counter ------------------------
    def sum_cost(self, since: str) -> Decimal:
        window = "date_trunc('day', now())" if since == "today" else "date_trunc('month', now())"
        row = self._q(f"SELECT COALESCE(SUM(cost_usd),0) AS c FROM runs WHERE started_at >= {window}", one=True)
        return Decimal(str(row["c"]))

    # -- write ----------------------------------------------------------------
    def create_work(self, kind, summary, rationale, requires_human=False) -> int:
        row = self._q(
            "INSERT INTO work (kind, summary, rationale, requires_human) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            (kind, summary, rationale, requires_human), one=True,
        )
        return row["id"]

    def start_run(self, work_id: int) -> int:
        self._q("UPDATE work SET status='running' WHERE id=%s", (work_id,))
        row = self._q("INSERT INTO runs (work_id) VALUES (%s) RETURNING id", (work_id,), one=True)
        return row["id"]

    def complete_run(self, cur, run_id: int, outcome: str, succeeded: bool, usage,
                     productive: bool = True, evidence: str = "",
                     measure_before=None, measure_after=None, escalation_level: str = "autonomous") -> None:
        cur.execute(
            "UPDATE runs SET completed_at=now(), outcome=%s, succeeded=%s, "
            "cost_usd=%s, tokens_in=%s, tokens_out=%s, "
            "productive=%s, evidence=%s, measure_before=%s, measure_after=%s, escalation_level=%s "
            "WHERE id=%s",
            (outcome, succeeded, usage.cost_usd, usage.tokens_in, usage.tokens_out,
             productive, evidence, measure_before, measure_after, escalation_level, run_id),
        )
        cur.execute("UPDATE work SET status='done' WHERE id=(SELECT work_id FROM runs WHERE id=%s)", (run_id,))

    def write_learning(self, cur, run_id, insight, evidence, scope, confidence) -> int:
        cur.execute(
            "INSERT INTO learnings (run_id, insight, evidence, scope, confidence) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (run_id, insight, evidence, scope, confidence),
        )
        return cur.fetchone()[0]

    def recent_productivity(self, limit: int = 15):
        """Newest first. The escalation ladder reads THIS — the database — not a counter in
        memory. A counter that resets on restart lets a stuck loop hammer forever by crashing."""
        return self._q(
            "SELECT id, coalesce(productive, false) AS productive, completed_at FROM runs "
            "WHERE completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT %s", (limit,))

    def fail_run(self, run_id: int, error: str) -> None:
        """A cycle that died leaves a row saying it died. Loud, not silent."""
        self._q(
            "UPDATE runs SET completed_at=now(), outcome=%s, succeeded=false, error=%s WHERE id=%s",
            (f"FAILED: {error[:400]}", error, run_id),
        )
        self._q("UPDATE work SET status='pending' WHERE id=(SELECT work_id FROM runs WHERE id=%s)", (run_id,))

    def abandon_run(self, run_id: int) -> None:
        """Rate-limited, not failed. Nothing went wrong — the plan is simply used up for now.

        We DELETE the run rather than marking it failed. A rate limit is not an outcome, and
        recording it as one would poison the history the loop learns from: the next cycle would
        "learn" from a failure that never happened. The work returns to pending and gets picked
        up when the plan resets.
        """
        self._q("UPDATE work SET status='pending' WHERE id=(SELECT work_id FROM runs WHERE id=%s)", (run_id,))
        self._q("DELETE FROM runs WHERE id=%s AND completed_at IS NULL", (run_id,))

    def beat(self, state: str, detail: str = "", retry_after_s: int | None = None) -> None:
        """Prove the process is ALIVE without claiming PROGRESS.

        A heartbeat can NEVER satisfy the loop-is-turning gate — that requires a completed run
        joined to a learning, and a heartbeat is neither. It exists purely so a supervisor can
        tell 'correctly waiting' from 'dead'.

        `retry_after_s` puts a DEADLINE on rate_limited. Without one, a loop stuck beating
        rate_limited forever reports HEALTHY forever — it isn't turning, it isn't hibernating, and
        nothing ever contradicts it. 'Waiting for the plan to reset' is valid only UNTIL the plan
        resets.

        The deadline is computed IN THE DATABASE (now() + interval), never from the process clock.
        A client clock is not a clock: a skewed or frozen one could hold itself healthy forever.
        """
        if retry_after_s is None:
            self._q("INSERT INTO heartbeat (state, detail) VALUES (%s,%s)", (state, detail[:200]))
        else:
            self._q(
                "INSERT INTO heartbeat (state, detail, retry_until) "
                "VALUES (%s, %s, now() + (%s || ' seconds')::interval)",
                (state, detail[:200], int(retry_after_s)))

    def last_beat(self):
        return self._q("SELECT state, beat_at, detail FROM heartbeat ORDER BY beat_at DESC LIMIT 1",
                       one=True)

    # -- hibernation: stopping must not mean abandonment -----------------------
    def open_hibernation(self):
        """The unresolved hibernation, if any. Survives restarts BY DESIGN — the whole point is
        that a reboot cannot wash away the fact that we are stuck and waiting."""
        return self._q(
            "SELECT * FROM hibernation WHERE resumed_at IS NULL ORDER BY hibernated_at DESC LIMIT 1",
            one=True)

    def hibernate(self, reason: str, unproductive: int) -> int:
        row = self._q(
            "INSERT INTO hibernation (reason, unproductive) VALUES (%s,%s) RETURNING id",
            (reason, unproductive), one=True)
        return row["id"]

    def mark_hibernation_notified(self, hid: int) -> None:
        """The human is told ONCE. Not once per systemd restart."""
        self._q("UPDATE hibernation SET notified_at=now() WHERE id=%s AND notified_at IS NULL", (hid,))

    def resume_signal(self, since) -> str | None:
        """Is there a REAL new signal to wake up for?

        Only two things count, and neither of them is the passage of time:
          * a human APPROVED parked work (they answered), or
          * a peer sent a message to the loop (someone else has something for us).

        Elapsed time is not a signal. A reboot is not a signal. If a restart could resume the
        loop, then 'stop spending until a human helps' would degrade into 'spin until the budget
        is gone', which is the exact failure hibernation exists to prevent.
        """
        r = self._q(
            "SELECT id, summary FROM work WHERE status='pending' AND approved_at > %s "
            "ORDER BY approved_at DESC LIMIT 1", (since,), one=True)
        if r:
            return f"human approved work #{r['id']}: {r['summary'][:60]}"
        try:
            m = self._q(
                "SELECT id, sender, subject FROM bb_messages "
                "WHERE recipient IN ('loop','*') AND created_at > %s "
                "ORDER BY created_at DESC LIMIT 1", (since,), one=True)
            if m:
                return f"message #{m['id']} from {m['sender']}: {m['subject'][:60]}"
        except Exception:
            pass  # blackboard not installed on this instance — fine, humans can still resume it
        return None

    def resume_hibernation(self, hid: int, signal: str) -> None:
        """Exactly once. The partial index guarantees we cannot resume the same one twice."""
        self._q(
            "UPDATE hibernation SET resumed_at=now(), resume_signal=%s "
            "WHERE id=%s AND resumed_at IS NULL", (signal, hid))

    def park_for_human(self, work_id: int) -> None:
        self._q("UPDATE work SET status='awaiting_human' WHERE id=%s", (work_id,))

    # -- graph ---------------------------------------------------------------
    def graph_link(self, cur, run_id: int, work_id: int, learning_id: int) -> None:
        """Mirror the cycle into the AGE graph: (Run)-[:EXECUTED]->(Work),
        (Learning)-[:DERIVED_FROM]->(Run). Rows say WHAT happened; the graph says what
        RESEMBLES what, which is how it reasons across its history instead of just
        accumulating it.

        If AGE isn't installed (graph: none in the interview), the relational history is
        still complete and the loop is still whole — the graph is a reasoning aid, not the
        system of record. We skip explicitly rather than failing.
        """
        if self.graph != "age":
            return
        cur.execute("LOAD 'age'; SET search_path = ag_catalog, \"$user\", public;")
        cur.execute(
            "SELECT * FROM cypher('aq', $$ "
            "  MERGE (w:Work {id: %(w)s}) MERGE (r:Run {id: %(r)s}) "
            "  MERGE (l:Learning {id: %(l)s}) "
            "  MERGE (r)-[:EXECUTED]->(w) MERGE (l)-[:DERIVED_FROM]->(r) "
            "$$) AS (v agtype);",
            {"w": work_id, "r": run_id, "l": learning_id},
        )

    # -- cross-instance sharing ------------------------------------------------
    #
    # A staged import is INERT. Note that live_learnings() above reads ONLY the `learnings` table —
    # a learning from another instance lives in `shared_learnings` and does not become a row in
    # `learnings` until it is PROMOTED here. That is not a convention; it is the reason a foreign
    # self-authored 'generalisable' flag cannot reach a single decide() call.

    def exportable_learnings(self):
        """Only what THIS instance believes would hold for a DIFFERENT mission — and only with the
        evidence attached, so the receiver can judge rather than trust."""
        return self._q(
            """SELECT l.id, l.insight, l.evidence, l.confidence, l.run_id,
                      r.outcome AS origin_outcome
               FROM learnings l LEFT JOIN runs r ON r.id = l.run_id
               WHERE l.superseded_by IS NULL AND l.scope = 'generalisable'
               ORDER BY l.confidence DESC""")

    def stage_import(self, **kw) -> int | None:
        """Receive a learning from elsewhere. STAGED — inert until adjudicated here."""
        row = self._q(
            """INSERT INTO shared_learnings
               (origin_instance, origin_mission, origin_run_id, origin_evidence, origin_outcome,
                insight, confidence, applies_when, falsified_by, imported_by)
               VALUES (%(origin_instance)s, %(origin_mission)s, %(origin_run_id)s,
                       %(origin_evidence)s, %(origin_outcome)s, %(insight)s, %(confidence)s,
                       %(applies_when)s, %(falsified_by)s, %(imported_by)s)
               ON CONFLICT (origin_instance, insight) DO NOTHING
               RETURNING id""", kw, one=True)
        return row["id"] if row else None

    def staged_imports(self):
        return self._q("SELECT * FROM shared_learnings WHERE status='staged' ORDER BY received_at")

    def promote_import(self, sid: int, by: str, why: str, evidence: dict | None = None) -> int:
        """Adopt an imported learning AS THIS INSTANCE'S OWN.

        A REASON IS NOT AN AUTHORIZATION. An eloquent sentence is precisely what a
        plausible-but-wrong claim attracts, and authorising on prose would leave one last path for
        foreign self-certification to become our executable truth — self-authored PROMOTION.

        So promotion demands that this instance show its own work:

            applies_here          did we evaluate the predicate against OUR mission? (must be True)
            applies_here_how      how we evaluated it
            negative_control      what we RAN that could have disproved it
            negative_control_result   what that actually returned
            evidence_ref          something re-checkable: a query, a file, a run id
            adjudicated_by        who. And for a high-confidence claim, NOT the importer.

        Missing any of these -> refused. The schema enforces it too (CHECK constraint), so a
        promoted row without local evidence is unrepresentable rather than merely discouraged.
        """
        s_row = self._q("SELECT * FROM shared_learnings WHERE id=%s AND status='staged'", (sid,), one=True)
        if not s_row:
            raise ValueError(f"no staged import #{sid}")

        e = evidence or {}
        required = ("applies_here_how", "negative_control", "negative_control_result", "evidence_ref")
        missing = [k for k in required if not str(e.get(k, "")).strip()]
        if e.get("applies_here") is not True or missing:
            raise PromotionRefused(
                f"REFUSED. A reason is not evidence.\n"
                f"  You wrote: {why!r}\n"
                f"  Missing local adjudication: {missing or []}"
                f"{'' if e.get('applies_here') is True else chr(10) + '  applies_here is not True — you have not shown this claim even applies to OUR mission.'}\n"
                f"  Promotion requires that THIS instance evaluated the predicate and RAN a negative "
                f"control that could have disproved the claim. Otherwise you are adopting a stranger's "
                f"self-certification because it was well phrased.")

        # Independence: for a high-confidence claim, whoever imported it may not also bless it.
        importer = s_row.get("imported_by")
        if s_row["confidence"] >= 0.8 and importer and importer == by:
            raise PromotionRefused(
                f"REFUSED. {by!r} imported this claim and is now adjudicating it. For a "
                f"high-confidence claim ({s_row['confidence']}), the adjudicator must be independent "
                f"of the importer — otherwise 'review' is the same actor agreeing with itself.")

        lid = self._q(
            """INSERT INTO learnings (run_id, insight, evidence, scope, confidence)
               SELECT id, %s, %s, 'generalisable', %s FROM runs ORDER BY id DESC LIMIT 1
               RETURNING id""",
            (s_row["insight"],
             f"IMPORTED from {s_row['origin_instance']} (their mission: {s_row['origin_mission']}). "
             f"THEIR evidence: {s_row['origin_evidence']}. "
             f"OUR negative control: {e['negative_control']} -> {e['negative_control_result']}. "
             f"Re-checkable at: {e['evidence_ref']}. Adjudicated by {by}: {why}",
             s_row["confidence"]), one=True)

        self._q(
            """UPDATE shared_learnings SET status='promoted', adjudicated_at=now(),
                   adjudicated_by=%s, adjudication=%s, local_learning_id=%s,
                   applies_here=true, applies_here_how=%s,
                   negative_control=%s, negative_control_result=%s, evidence_ref=%s
               WHERE id=%s""",
            (by, why, lid["id"], e["applies_here_how"], e["negative_control"],
             e["negative_control_result"], e["evidence_ref"], sid))
        return lid["id"]

    def reject_import(self, sid: int, by: str, why: str) -> None:
        self._q("UPDATE shared_learnings SET status='rejected', adjudicated_at=now(), "
                "adjudicated_by=%s, adjudication=%s WHERE id=%s AND status='staged'", (by, why, sid))

    def rollback_import(self, sid: int, by: str, why: str) -> None:
        """It was promoted, and it turned out to be wrong HERE. Retract it locally — without
        arguing with the instance it came from. Their mission is not ours."""
        s = self._q("SELECT local_learning_id FROM shared_learnings WHERE id=%s", (sid,), one=True)
        if s and s["local_learning_id"]:
            self._q("DELETE FROM learnings WHERE id=%s", (s["local_learning_id"],))
        self._q("UPDATE shared_learnings SET status='rolled_back', adjudicated_at=now(), "
                "adjudicated_by=%s, adjudication=%s WHERE id=%s", (by, why, sid))
