"""The durable history. Everything the system knows, and the only thing it trusts.

Design rule running through this file: the database is GROUND TRUTH and memory is not.
Costs are summed from rows, the mission's number is re-read from its real source, and the
record+learn write is one transaction. Nothing important is held in a process that a crash
can reset.
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal

import psycopg2
import psycopg2.extras

from .approval import assert_valid_approval

log = logging.getLogger("aq.db")

APPROVED_SIDE_EFFECTS_WARNING = (
    "Side effects UNKNOWN — verify before re-approving. The approved act may already have "
    "changed the world."
)


def is_goal_relaxation_decision(decision) -> bool:
    """Terminal decisions without a chosen mode (unknown/all-blocked) are not proposals."""
    return getattr(decision.chosen_mode, "value", None) == "goal_relaxation"


def bind_acquisition_instruction(mode_instruction: str, forecast_detail: str) -> str:
    """Make immutable mode semantics and the scored detail jointly binding for ACT."""
    if not mode_instruction.strip() or not forecast_detail.strip():
        raise ValueError("acquisition instructions must include mode contract and forecast detail")
    return (f"{mode_instruction}\n\nFORECAST-SPECIFIC DETAIL (also binding): "
            f"{forecast_detail}")


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
    expected_cost_usd: Decimal = Decimal("0")
    blast_radius: float = 0.0
    touches_human: bool = False
    commits: bool = False
    plan_id: str | None = None
    plan: dict | None = None
    acquisition_id: int | None = None
    acquisition_rung: str | None = None
    meta_mode_decision_id: int | None = None
    intent_valid: bool | None = None
    intent_reasons: tuple[str, ...] = ()
    expected_expense_usd: Decimal = Decimal("0")
    blast_radius_level: int = 3
    blast_radius_basis: dict | None = None
    gate_policy_version: str | None = None
    gate_reason: str | None = None


class Db:
    def __init__(self, url: str, graph: str = "age", connect_retries: int = 30) -> None:
        self.url = url
        self.graph = graph
        # RETRY THE CONNECTION ON STARTUP.
        #
        # On a real Windows reboot the loop's systemd service started BEFORE Postgres was ready and
        # crashed; Restart=always masked it that time. Do not rely on a crash+restart to paper over
        # a startup race — the unit's After=postgresql.service is a no-op on WSL2 (the cluster is not
        # managed by that unit). So wait for the DB to actually accept us. This is the same
        # installed-vs-running distinction the whole kit is about, applied to our own startup.
        import time as _t
        last = None
        for attempt in range(connect_retries):
            try:
                self.conn = psycopg2.connect(url)
                self.conn.autocommit = True
                if attempt:
                    log.info("connected to postgres after %d ret(ies) — it was not ready at startup", attempt)
                return
            except psycopg2.OperationalError as e:
                last = e
                _t.sleep(2)
        raise RuntimeError(
            f"could not connect to postgres after {connect_retries} tries ({connect_retries*2}s). "
            f"Is the server running? Last error: {last}")

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

    def read_scalar(self, query: str) -> Decimal:
        """Run an arbitrary single-number query (used for a live target_query). Same ground-truth
        discipline as read_measure: run the real query, do not trust a cached copy."""
        row = self._q(query, one=True)
        if not row:
            raise MeasureUnreadable(f"target_query returned no rows: {query!r}")
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

    def approved_work(self):
        """The oldest human-approved work waiting to execute.

        Approval is represented by pending + approved_at. Plain pending work can come from normal
        autonomous decisions or interrupted retries; only approved_at proves a human released
        parked work.
        """
        return self._q(
            "SELECT w.*,pa.acquisition_id,pa.rung AS acquisition_rung,"
            "pa.meta_mode_decision_id,"
            "pa.instruction AS acquisition_instruction,d.expected_expense_usd AS acquisition_expense,"
            "d.blast_radius_level AS acquisition_blast,d.reversible AS acquisition_reversible,"
            "d.spends_money AS acquisition_spends,d.touches_human AS acquisition_human,"
            "d.commits AS acquisition_commits FROM work w "
            "LEFT JOIN LATERAL (SELECT * FROM plan_acquisition WHERE work_id=w.id AND status='pending' "
            " ORDER BY acquisition_id LIMIT 1) pa ON true "
            "LEFT JOIN impasse_meta_mode_decision d ON d.decision_id=pa.meta_mode_decision_id "
            "WHERE w.status='pending' AND w.approved_at IS NOT NULL "
            "AND w.execution_path='mission' "
            "ORDER BY w.approved_at,w.created_at LIMIT 1",
            one=True,
        )

    def pending_autonomous_work(self):
        """Resume the oldest autonomous plan instead of asking DECIDE to recreate it.

        Acquisition cycles deliberately return the original work to pending. This is the durable
        continuation that lets recall advance to search rather than resetting at a new work row.
        """
        return self._q(
            "SELECT w.*,pa.acquisition_id,pa.rung AS acquisition_rung,"
            "pa.meta_mode_decision_id,"
            "pa.instruction AS acquisition_instruction,d.expected_expense_usd AS acquisition_expense,"
            "d.blast_radius_level AS acquisition_blast,d.reversible AS acquisition_reversible,"
            "d.spends_money AS acquisition_spends,d.touches_human AS acquisition_human,"
            "d.commits AS acquisition_commits FROM work w "
            "LEFT JOIN LATERAL (SELECT * FROM plan_acquisition WHERE work_id=w.id AND status='pending' "
            " ORDER BY acquisition_id LIMIT 1) pa ON true "
            "LEFT JOIN impasse_meta_mode_decision d ON d.decision_id=pa.meta_mode_decision_id "
            "WHERE w.status='pending' AND w.approved_at IS NULL "
            "AND w.execution_path='mission' "
            "ORDER BY w.created_at LIMIT 1",
            one=True,
        )

    def recent_runs(self, limit: int = 10):
        return self._q(
            "SELECT r.id, r.outcome, r.succeeded, r.rolled_back, w.kind, w.summary "
            "FROM runs r JOIN work w ON w.id=r.work_id "
            "WHERE r.completed_at IS NOT NULL ORDER BY r.completed_at DESC LIMIT %s",
            (limit,),
        )

    # -- self-correction consultant SHADOW (stage 1, observe-only, #4834) -------
    # These three are the entire live surface of the stage-1 shadow: two READ-ONLY snapshot
    # reads over the governed principle tables, and one append-only telemetry write. Nothing
    # here mutates governance state or the loop's decision.
    # A disposition OVERTURN is a transition that changes a principle's settled status
    # (from_status present AND to_status <> from_status): a promotion or a demotion/withdrawal.
    # This excludes 'mined' (from_status NULL) and shadow_test/validation (to_status == from_status).
    # Normalization mirrors runner.consultants.self_correction.normalize_rule_identity: strip a
    # trailing version suffix (e.g. '-v1'/'_v2') and casefold, so v1/v2 of a rule are one class.
    _RULE_NORMALIZE = "([._-](v|rev|version)(\\.?[0-9]+)+)+$"

    def self_correction_recent_correction(self, window: str = "30 days"):
        """READ-ONLY. The most recent governance CORRECTION in the governed principle tables.

        A correction is the most recent disposition OVERTURN — a transition that changed a
        principle's settled status (a demotion/withdrawal or a promotion), recorded by the trusted
        governance/evaluator principals (aq_loop cannot write any principle transition, so the
        identity is trusted; it is not literally a human). Returns the overturned lineage's
        MINED-transition id (its stable principle id) and MINED rule_version (the generating rule,
        per the documented mapping — NOT the overturn transition's own rule_version), plus the
        from/to statuses, the overturning transition kind/principal, and when. None when there is
        no recent correction inside `window`.
        """
        return self._q(
            "SELECT mined.id AS principle_id, mined.rule_version AS rule_version, "
            "       t.from_status, t.to_status, t.transition_kind, t.transitioned_by, t.automatic, "
            "       t.created_at AS corrected_at "
            "FROM causal_principle_transition t "
            "JOIN LATERAL (SELECT id, rule_version FROM causal_principle_transition m "
            "  WHERE m.cause=t.cause AND m.effect=t.effect AND m.scope=t.scope "
            "  AND m.transition_kind='mined' ORDER BY m.id LIMIT 1) mined ON true "
            "WHERE t.from_status IS NOT NULL AND t.to_status IS DISTINCT FROM t.from_status "
            "  AND t.created_at >= now() - %s::interval "
            "ORDER BY t.created_at DESC, t.id DESC LIMIT 1",
            (window,),
            one=True,
        )

    def self_correction_rule_principles(self, rule_version: str):
        """READ-ONLY. Every principle lineage whose generating rule is the SAME NORMALIZED
        identity as `rule_version` (so v1/v2 variants are one sibling class), with its current
        classification (latest to_status) and its own most-recent disposition validation."""
        return self._q(
            "WITH mined AS ("
            "  SELECT DISTINCT ON (cause,effect,scope) id AS principle_id, cause, effect, scope, "
            "         rule_version FROM causal_principle_transition WHERE transition_kind='mined' "
            "  ORDER BY cause,effect,scope, id), "
            "latest AS ("
            "  SELECT DISTINCT ON (cause,effect,scope) cause, effect, scope, to_status "
            "  FROM causal_principle_transition ORDER BY cause,effect,scope, id DESC), "
            "validated AS ("
            "  SELECT DISTINCT ON (cause,effect,scope) cause, effect, scope, "
            "         to_status AS validated_classification, created_at AS validated_at, evidence_ref "
            "  FROM causal_principle_transition "
            "  WHERE from_status IS NOT NULL AND to_status IS DISTINCT FROM from_status "
            "  ORDER BY cause,effect,scope, created_at DESC, id DESC) "
            "SELECT m.principle_id, m.rule_version AS generating_rule_id, l.to_status AS classification, "
            "       v.validated_classification, v.validated_at, v.evidence_ref "
            "FROM mined m JOIN latest l USING (cause,effect,scope) "
            "LEFT JOIN validated v USING (cause,effect,scope) "
            "WHERE lower(regexp_replace(btrim(m.rule_version), %(re)s, '', 'i')) "
            "    = lower(regexp_replace(btrim(%(rule)s), %(re)s, '', 'i')) "
            "ORDER BY m.principle_id",
            {"rule": rule_version, "re": self._RULE_NORMALIZE},
        )

    def record_self_correction_shadow(self, *, work_context, correction_item_id, generating_rule_id,
                                       result_kind, action, rationale, reopened_principle_ids,
                                       requires_human, detail=None):
        """APPEND-ONLY telemetry — the ONLY write the stage-1 shadow ever makes."""
        self._q(
            "INSERT INTO self_correction_shadow_log(work_context,correction_item_id,generating_rule_id,"
            "result_kind,action,rationale,reopened_principle_ids,requires_human,detail) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb)",
            (work_context, correction_item_id, generating_rule_id, result_kind, action, rationale,
             json.dumps(list(reopened_principle_ids or [])), bool(requires_human),
             json.dumps(detail or {})),
        )

    def enqueue_self_correction_audit_request(self, *, principle_id, generating_rule_id, reason,
                                              source_correction, source_correction_item_id=None,
                                              source_corrected_at=None, work_context=None,
                                              detail=None) -> bool:
        """STAGE-2 sibling_audit apply (#4834). APPEND-ONLY + IDEMPOTENT.

        Enqueue ONE bounded re-examination REQUEST for a suspect same-rule sibling. This is a
        review request only: it NEVER promotes, demotes, or authorizes a principle (aq_loop cannot
        write any principle transition — schema/999). ``ON CONFLICT DO NOTHING`` on the
        (principle_id, source_correction) UNIQUE key makes re-enqueuing the same correction a no-op,
        so the same correction twice cannot open a duplicate request. Returns True when a NEW row
        was inserted, False when a duplicate was suppressed.

        Deliberately NO ``RETURNING`` clause and an UN-TARGETED ``ON CONFLICT DO NOTHING``:
        insertion is detected via ``rowcount`` so aq_loop needs only INSERT (not SELECT) on the
        queue, exactly like the stage-1 shadow log (schema/026). A targeted ``ON CONFLICT (cols)``
        would require SELECT (PG infers the arbiter index); the un-targeted form does not, and the
        only non-serial unique key on the table is the idempotency constraint, so the two forms are
        equivalent here.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO self_correction_audit_request"
                "(principle_id,generating_rule_id,reason,source_correction,source_correction_item_id,"
                " source_corrected_at,work_context,detail) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb) "
                "ON CONFLICT DO NOTHING",
                (str(principle_id), str(generating_rule_id), reason, str(source_correction),
                 (str(source_correction_item_id) if source_correction_item_id is not None else None),
                 source_corrected_at, work_context, json.dumps(detail or {})),
            )
            return cur.rowcount == 1

    def record_self_correction_arbiter_action(self, *, work_context, outcome, action, held_dispatch,
                                              correction_item_id, generating_rule_id, reason,
                                              audit_request_count=0, detail=None) -> None:
        """APPEND-ONLY "records why" trail for an APPLIED stage-2 arbiter action (#4834)."""
        self._q(
            "INSERT INTO self_correction_arbiter_action"
            "(work_context,outcome,action,held_dispatch,correction_item_id,generating_rule_id,"
            " reason,audit_request_count,detail) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (work_context, outcome, action, bool(held_dispatch),
             (str(correction_item_id) if correction_item_id is not None else None),
             (str(generating_rule_id) if generating_rule_id is not None else None),
             reason, int(audit_request_count), json.dumps(detail or {})),
        )

    def beat_process(self, pid: int) -> None:
        """Observed process liveness only. This never counts as progress or a completed cycle."""
        self._q(
            "INSERT INTO loop_runtime(singleton,pid,started_at,beat_at) "
            "VALUES (true,%s,now(),now()) "
            "ON CONFLICT(singleton) DO UPDATE SET pid=excluded.pid, "
            "started_at=CASE WHEN loop_runtime.pid=excluded.pid THEN loop_runtime.started_at ELSE now() END, "
            "beat_at=now()",
            (pid,),
        )

    def beat_cycle(self) -> None:
        """DEPLOY-HYGIENE liveness (#4834): record that a real DECIDE cycle just completed.

        A single-row upsert of last_cycle_at + a monotonic cycle_count. Unlike beat_process (a 2s
        PROCESS pulse) this proves the loop is actually TURNING, and unlike beat() it is not a
        progress/authority signal — it can never satisfy the loop-is-turning gate. /health reads it
        so a crash-looping-but-up loop reads as NOT cycling instead of silently 'healthy'.
        """
        self._q(
            "INSERT INTO loop_heartbeat(singleton,last_cycle_at,cycle_count) "
            "VALUES (true, now(), 1) "
            "ON CONFLICT(singleton) DO UPDATE SET "
            "last_cycle_at=now(), cycle_count=loop_heartbeat.cycle_count+1"
        )

    # -- budget: summed from ROWS, never from a counter ------------------------
    def sum_cost(self, since: str) -> Decimal:
        window = "date_trunc('day', now())" if since == "today" else "date_trunc('month', now())"
        row = self._q(
            f"SELECT (SELECT COALESCE(SUM(cost_usd),0) FROM runs WHERE started_at >= {window}) + "
            f"(SELECT COALESCE(SUM(cost_usd),0) FROM impasse_meta_mode_forecast_attempt "
            f" WHERE created_at >= {window}) + "
            f"(SELECT COALESCE(SUM(d.cost_usd),0) FROM impasse_meta_mode_decision d "
            f" WHERE d.created_at >= {window} AND NOT EXISTS "
            f" (SELECT 1 FROM impasse_meta_mode_forecast_attempt a WHERE a.decision_id=d.decision_id)) AS c",
            one=True)
        return Decimal(str(row["c"]))

    def sum_plan_expense(self) -> Decimal:
        row = self._q(
            "SELECT (SELECT COALESCE(SUM(expected_expense_usd),0) FROM plan_spend_reservation "
            " WHERE status IN ('reserved','incurred')) + "
            "(SELECT COALESCE(SUM(expected_expense_usd),0) FROM meta_mode_spend_reservation "
            " WHERE status IN ('reserved','incurred')) AS c", one=True,
        )
        return Decimal(str(row["c"]))

    def reserve_plan_expense(self, work_id: int, amount: Decimal) -> None:
        self._q(
            "INSERT INTO plan_spend_reservation(work_id,expected_expense_usd) VALUES(%s,%s) "
            "ON CONFLICT(work_id) DO NOTHING", (work_id, amount),
        )

    def meta_mode_expense_reservation(self, decision_id: int) -> Decimal | None:
        row = self._q(
            "SELECT expected_expense_usd FROM meta_mode_spend_reservation WHERE decision_id=%s",
            (decision_id,), one=True)
        return None if row is None else Decimal(str(row["expected_expense_usd"]))

    def reserve_meta_mode_expense(self, decision_id: int, amount: Decimal) -> None:
        self._q(
            "INSERT INTO meta_mode_spend_reservation(decision_id,expected_expense_usd) "
            "VALUES(%s,%s) ON CONFLICT(decision_id) DO NOTHING", (decision_id, amount),
        )

    def mark_plan_expense_incurred(self, work_id: int) -> None:
        self._q(
            "UPDATE plan_spend_reservation SET status='incurred',incurred_at=now() "
            "WHERE work_id=%s AND status='reserved'", (work_id,),
        )

    def mark_meta_mode_expense_incurred(self, decision_id: int) -> None:
        self._q(
            "UPDATE meta_mode_spend_reservation SET status='incurred',incurred_at=now() "
            "WHERE decision_id=%s AND status='reserved'", (decision_id,),
        )

    def curiosity_cost_today(self) -> Decimal:
        row = self._q(
            "SELECT COALESCE(SUM(r.cost_usd),0) AS c "
            "FROM runs r JOIN work w ON w.id = r.work_id "
            "WHERE w.kind='curiosity' AND r.started_at >= date_trunc('day', now())",
            one=True,
        )
        return Decimal(str(row["c"]))

    def curiosity_cycles_today(self) -> int:
        row = self._q(
            "SELECT COUNT(*) AS n "
            "FROM runs r JOIN work w ON w.id = r.work_id "
            "WHERE w.kind='curiosity' AND r.completed_at IS NOT NULL "
            "AND r.started_at >= date_trunc('day', now())",
            one=True,
        )
        return int(row["n"])

    # -- write ----------------------------------------------------------------
    def create_work(self, kind, summary, rationale, requires_human=False,
                    plan_id=None, plan=None, expected_expense_usd=None,
                    blast_radius_level=None, blast_radius_basis=None,
                    gate_policy_version=None, gate_reason=None, *,
                    reversible=True, spends_money=False, expected_cost_usd=None,
                    blast_radius=None, touches_human=False, commits=False) -> int:
        """Persist the composed A+B plan contract and Track-E observability fields together.

        The structured plan values are authoritative.  The scalar cost/radius columns are a
        compatibility projection for the management UI, never a second gate decision.
        """
        expense = Decimal(str(expected_expense_usd or 0))
        radius_level = 0 if blast_radius_level is None else int(blast_radius_level)
        ui_cost = expense if expected_cost_usd is None else Decimal(str(expected_cost_usd))
        ui_radius = radius_level / 3.0 if blast_radius is None else float(blast_radius)
        row = self._q(
            "INSERT INTO work (kind,summary,rationale,requires_human,plan_id,plan,"
            "expected_expense_usd,blast_radius_level,blast_radius_basis,gate_policy_version,gate_reason,"
            "reversible,spends_money,expected_cost_usd,blast_radius,touches_human,commits) "
            "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (kind, summary, rationale, requires_human, plan_id,
             psycopg2.extras.Json(plan) if plan is not None else None,
             expense, radius_level,
             psycopg2.extras.Json(blast_radius_basis) if blast_radius_basis is not None else None,
             gate_policy_version, gate_reason, reversible, spends_money, ui_cost, ui_radius,
             touches_human, commits), one=True,
        )
        return row["id"]

    def known_plan_relations(self):
        """Relations usable by the direction-first DECIDE check.

        NULL direction means the old edge does not assert enough to govern a step;
        it is deliberately not fabricated as `toward`.
        """
        return self._q(
            "SELECT e.edge_id, e.source_action, e.direct_effect, e.relation_direction, "
            "e.mechanism_description, e.scope_conditions, e.predicted_certainty, "
            "e.supplier_principle_id, latest.promotion_transition_id "
            "FROM causal_edge e "
            "JOIN LATERAL ("
            "  SELECT t.to_status, t.authority_after, "
            "    (SELECT p.id FROM causal_principle_transition p "
            "     WHERE p.cause=t.cause AND p.effect=t.effect AND p.scope=t.scope "
            "       AND p.transition_kind='promote' ORDER BY p.id DESC LIMIT 1) "
            "       AS promotion_transition_id "
            "  FROM causal_principle_transition t "
            "  WHERE t.cause=e.source_action AND t.effect=e.direct_effect "
            "    AND t.scope::jsonb=e.scope_conditions::jsonb "
            "  ORDER BY t.id DESC LIMIT 1"
            ") latest ON latest.to_status='promoted' AND latest.authority_after=true "
            "WHERE e.relation_direction IS NOT NULL AND e.falsified_by IS NULL"
        )

    def record_plan_predictions(self, work_id: int, plan_id: str, assessments) -> None:
        """Atomically persist every step assertion; idempotent across approve/restart."""
        with self.tx() as cur:
            for step, result in assessments:
                edge_id = int(result.relation_id) if result.relation_id is not None else None
                cur.execute(
                    "INSERT INTO planning_prediction "
                    "(edge_id, plan_id, work_id, step_id, step_index, source_action, "
                    " expected_direct_effect, expected_direction, scope, edge_state, sufficient, "
                    " predicted_certainty, assessment_annotation,supplier_principle_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (work_id, step_id) WHERE work_id IS NOT NULL AND step_id IS NOT NULL "
                    "DO NOTHING",
                    (edge_id, plan_id, work_id, step["step_id"], step["step_index"],
                     step["action"], step["expected_effect"], step["expected_direction"],
                     psycopg2.extras.Json(step.get("scope") or {}), result.edge_state.value,
                     result.sufficient, result.certainty, result.annotation,
                     result.principle_id),
                )
            expected_ids = [step["step_id"] for step, _ in assessments]
            cur.execute(
                "SELECT step_id FROM planning_prediction WHERE work_id=%s AND step_id = ANY(%s)",
                (work_id, expected_ids),
            )
            recorded_ids = {row[0] for row in cur.fetchall()}
            if recorded_ids != set(expected_ids):
                raise RuntimeError(
                    f"pre-ACT prediction invariant failed for work {work_id}: "
                    f"expected {expected_ids!r}, found {sorted(recorded_ids)!r}"
                )

    def prepare_acquisition_step(self, work_id: int, plan_id: str, target_step_id: str,
                                 *, include_analogy: bool = True):
        """Persist and return the next rung plus its own pre-ACT direction assertion."""
        from .acquisition import next_acquisition_step

        rows = self._q(
            "SELECT rung FROM plan_acquisition WHERE work_id=%s AND target_step_id=%s "
            "AND status IN ('completed','skipped')",
            (work_id, target_step_id),
        ) or []
        step = next_acquisition_step(
            target_step_id, [row["rung"] for row in rows], include_analogy=include_analogy
        )
        if step is None:
            self.park_for_human(
                work_id,
                note=f"acquisition ladder exhausted for plan step {target_step_id}; target remains unsupported",
            )
            return None
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO plan_acquisition "
                "(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction,proposer_only) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (work_id,target_step_id,rung) DO UPDATE SET instruction=EXCLUDED.instruction "
                "RETURNING acquisition_id,work_id,plan_id,target_step_id,rung,rung_index,"
                "action_step_id,instruction,proposer_only,status",
                (work_id, plan_id, target_step_id, step.rung.value, step.rung_index,
                 step.action_step_id, step.instruction, step.proposer_only),
            )
            columns = [desc.name for desc in cur.description]
            acquisition = dict(zip(columns, cur.fetchone()))
            # The acquisition itself is a real action.  Assert its intended direction before ACT;
            # its absent edge is honest rather than recursive refusal.
            cur.execute(
                "INSERT INTO planning_prediction "
                "(edge_id,plan_id,work_id,step_id,step_index,source_action,expected_direct_effect,"
                " expected_direction,scope,edge_state,sufficient,predicted_certainty,assessment_annotation) "
                "VALUES (NULL,%s,%s,%s,%s,%s,%s,'toward','{}'::jsonb,'absent',false,0,%s) "
                "ON CONFLICT (work_id,step_id) WHERE work_id IS NOT NULL AND step_id IS NOT NULL "
                "DO NOTHING",
                (plan_id, work_id, step.action_step_id, -1 + step.rung_index + 1,
                 f"knowledge_acquisition:{step.rung.value}", "missing direction becomes known",
                 f"acquisition for localized gap {target_step_id}"),
            )
        return acquisition

    def meta_mode_observations(self, work_id: int, target_step_id: str) -> list[dict]:
        """Return only durable completed acquisition observations for sequential rescoring."""
        return self._q(
            "SELECT acquisition_id,rung,result,completed_at FROM plan_acquisition "
            "WHERE work_id=%s AND target_step_id=%s AND status='completed' "
            "ORDER BY completed_at,acquisition_id", (work_id, target_step_id),
        ) or []

    def record_meta_mode_forecast_attempt(self, work_id: int, plan_id: str,
                                          target_step_id: str, forecast: dict, usage) -> int:
        row = self._q(
            "INSERT INTO impasse_meta_mode_forecast_attempt "
            "(work_id,plan_id,target_step_id,raw_forecast,tokens_in,tokens_out,cost_usd) "
            "VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s) RETURNING attempt_id",
            (work_id, plan_id, target_step_id, psycopg2.extras.Json(forecast),
             int(usage.tokens_in), int(usage.tokens_out), usage.cost_usd), one=True)
        return int(row["attempt_id"])

    def reject_meta_mode_forecast_attempt(self, attempt_id: int, error: str) -> None:
        with self.tx() as cur:
            cur.execute(
                "UPDATE impasse_meta_mode_forecast_attempt SET validation_error=%s "
                "WHERE attempt_id=%s AND validation_error IS NULL RETURNING work_id",
                (error[:1000], attempt_id))
            row = cur.fetchone()
            if row is None:
                return
            cur.execute(
                "UPDATE work SET status='awaiting_human',approved_at=NULL, "
                "rationale=rationale || %s WHERE id=%s",
                (f"\n\nMeta-mode forecast rejected and parked: {error[:500]}", int(row[0])))

    def prepare_meta_mode_decision(self, work_id: int, plan_id: str, target_step_id: str,
                                   decision, usage, attempt_id: int | None = None) -> dict | None:
        """Atomically persist scored choice, forecast cost, and optional acquisition."""
        from .acquisition import acquisition_step_for_mode
        observations = self.meta_mode_observations(work_id, target_step_id)
        observation_index = len(observations)
        payload = decision.json()
        chosen = next((card for card in payload["scorecards"]
                       if card["mode"] == payload["chosen_mode"]), {})
        consequence = {
            "expense": Decimal(str(chosen.get("expected_expense_usd") or 0)),
            "blast": int(chosen.get("blast_radius_level") or 0),
            "reversible": bool(chosen.get("reversible", True)),
            "spends": bool(chosen.get("spends_money", False)),
            "human": bool(chosen.get("touches_human", False)),
            "commits": bool(chosen.get("commits", False)),
        }
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO impasse_meta_mode_decision "
                "(work_id,plan_id,target_step_id,observation_index,policy_version,scorecards,"
                " decision,chosen_mode,chosen_score,stop_reason,chosen_instruction,"
                " expected_expense_usd,blast_radius_level,reversible,spends_money,touches_human,"
                " commits,tokens_in,tokens_out,cost_usd) "
                "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "RETURNING decision_id",
                (work_id, plan_id, target_step_id, observation_index,
                 payload["policy_version"], psycopg2.extras.Json(payload["scorecards"]),
                 payload["decision"], payload["chosen_mode"], payload["chosen_score"],
                 payload["stop_reason"], payload["chosen_instruction"], consequence["expense"],
                 consequence["blast"], consequence["reversible"], consequence["spends"],
                 consequence["human"], consequence["commits"], int(usage.tokens_in),
                 int(usage.tokens_out), usage.cost_usd),
            )
            decision_id = int(cur.fetchone()[0])
            if attempt_id is not None:
                cur.execute(
                    "UPDATE impasse_meta_mode_forecast_attempt SET decision_id=%s "
                    "WHERE attempt_id=%s AND decision_id IS NULL",
                    (decision_id, attempt_id))
                if cur.rowcount != 1:
                    raise RuntimeError("meta-mode forecast attempt was already linked")
            if decision.decision != "acquire":
                if is_goal_relaxation_decision(decision):
                    cur.execute(
                        "UPDATE work SET status='awaiting_human',approved_at=NULL, "
                        "kind='goal_relaxation_proposal',summary=%s, "
                        "rationale=rationale || %s WHERE id=%s AND status='pending'",
                        (decision.chosen_instruction,
                         f"\n\nGoal-relaxation proposal: {decision.chosen_instruction}", work_id))
                else:
                    cur.execute("UPDATE work SET status='abandoned' WHERE id=%s AND status='pending'",
                                (work_id,))
                if cur.rowcount != 1:
                    raise RuntimeError(f"work #{work_id} was not pending at meta-mode stop")
                return None
            step = acquisition_step_for_mode(
                target_step_id, decision.chosen_mode.value, observation_index)
            # Bind the immutable mode contract and the exact scored detail into one instruction.
            # The forecaster cannot replace sandbox/no-target/human-receipt semantics by relabeling
            # an unsafe instruction as a benign mode.
            instruction = bind_acquisition_instruction(
                step.instruction, decision.chosen_instruction)
            cur.execute(
                "INSERT INTO plan_acquisition "
                "(work_id,plan_id,target_step_id,rung,rung_index,action_step_id,instruction,"
                " proposer_only,meta_mode_decision_id) VALUES (%s,%s,%s,%s,%s,%s,%s,false,%s) "
                "RETURNING acquisition_id,work_id,plan_id,target_step_id,rung,rung_index,"
                "action_step_id,instruction,proposer_only,status,meta_mode_decision_id",
                (work_id, plan_id, target_step_id, step.rung.value, step.rung_index,
                 step.action_step_id, instruction, decision_id),
            )
            columns = [desc.name for desc in cur.description]
            acquisition = dict(zip(columns, cur.fetchone()))
            acquisition.update(expected_expense_usd=consequence["expense"],
                               blast_radius_level=consequence["blast"],
                               reversible=consequence["reversible"],
                               spends_money=consequence["spends"],
                               touches_human=consequence["human"], commits=consequence["commits"])
            cur.execute(
                "INSERT INTO planning_prediction "
                "(edge_id,plan_id,work_id,step_id,step_index,source_action,expected_direct_effect,"
                " expected_direction,scope,edge_state,sufficient,predicted_certainty,assessment_annotation) "
                "VALUES (NULL,%s,%s,%s,%s,%s,%s,'toward','{}'::jsonb,'absent',false,0,%s) "
                "ON CONFLICT (work_id,step_id) WHERE work_id IS NOT NULL AND step_id IS NOT NULL "
                "DO NOTHING",
                (plan_id, work_id, step.action_step_id, observation_index,
                 f"knowledge_acquisition:{step.rung.value}", "decision-relevant uncertainty reduced",
                 f"meta-mode acquisition for localized gap {target_step_id}"),
            )
        return acquisition

    def retire_goal_relaxation_proposal(self, work_id: int) -> None:
        """Acknowledge an approved proposal without pretending it mutated mission configuration."""
        self._q(
            "UPDATE work SET status='abandoned',approved_at=NULL, "
            "rationale=rationale || %s WHERE id=%s AND kind='goal_relaxation_proposal' "
            "AND status='pending' AND approved_at IS NOT NULL",
            ("\n\nProposal acknowledged and retired; mission changes require an explicit operator edit.",
             work_id))

    def wake_impasse_stops(self) -> int:
        """Reopen stopped work only when a new authoritative in-scope direction arrives.

        A raw ``causal_edge`` is candidate data, not authority: the acquisition trigger creates it
        together with a provisional lifecycle transition.  Waking on that row used to let a
        provisional (even directionless) proposal change durable planning state before the
        promoted-only pre-ACT check could reject it.  Mirror that check's lifecycle, direction,
        falsification, and scope boundary here.
        """
        rows = self._q("""
            UPDATE work w SET status='pending'
             WHERE w.status='abandoned'
               AND w.kind <> 'goal_relaxation_proposal'
               AND EXISTS (
                 SELECT 1 FROM impasse_meta_mode_decision d
                 CROSS JOIN LATERAL jsonb_array_elements(w.plan->'steps') step
                 JOIN causal_edge e ON e.source_action=step->>'action'
                                   AND e.direct_effect=step->>'expected_effect'
                 JOIN LATERAL (
                   SELECT t.to_status,t.authority_after,
                     (SELECT p.created_at FROM causal_principle_transition p
                      WHERE p.cause=t.cause AND p.effect=t.effect AND p.scope=t.scope
                        AND p.transition_kind='promote' ORDER BY p.id DESC LIMIT 1)
                       AS authority_created_at
                   FROM causal_principle_transition t
                   WHERE t.cause=e.source_action AND t.effect=e.direct_effect
                     AND t.scope::jsonb=e.scope_conditions::jsonb
                   ORDER BY t.id DESC LIMIT 1
                 ) latest ON latest.to_status='promoted' AND latest.authority_after=true
                WHERE d.work_id=w.id AND d.created_at < latest.authority_created_at
                  AND d.observation_index=(SELECT max(d2.observation_index)
                                             FROM impasse_meta_mode_decision d2 WHERE d2.work_id=w.id)
                  AND step->>'step_id'=d.target_step_id
                  AND e.relation_direction IS NOT NULL AND e.falsified_by IS NULL
                  AND coalesce(step->'scope','{}'::jsonb) @> e.scope_conditions::jsonb)
            RETURNING w.id
        """) or []
        return len(rows)


    def mark_acquisition_running(self, acquisition_id: int) -> None:
        with self.tx() as cur:
            cur.execute(
                "UPDATE plan_acquisition SET status='running' "
                "WHERE acquisition_id=%s AND status='pending'",
                (acquisition_id,),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"acquisition #{acquisition_id} was not pending at start")

    def stage_acquired_relations(self, cur, run_id: int, work: Work, proposals) -> list[int]:
        """Validate ACT candidates for the acquisition receipt without granting authority.

        The actor-authored proposals remain inside ``plan_acquisition.result`` when the acquisition
        closes. They are only submissions: the separately credentialed governance writer must
        attest that completed receipt before an edge can be staged. Returning no edge ids is the
        honest pre-attestation disposition and preserves Scope A's inert-proposal behavior.
        """
        if work.acquisition_id is None:
            if proposals:
                raise ValueError("causal proposals are accepted only from acquisition work")
            return []
        raw_steps = (work.plan or {}).get("steps") or []
        cur.execute("SELECT target_step_id FROM plan_acquisition WHERE acquisition_id=%s",
                    (work.acquisition_id,))
        target_row = cur.fetchone()
        if target_row is None:
            raise ValueError("unknown acquisition")
        target = next((s for s in raw_steps if str(s.get("step_id")) == str(target_row[0])), None)
        if target is None:
            raise ValueError("acquisition target step is absent from its durable plan")
        inserted: list[int] = []
        for proposal in proposals or []:
            action = str(proposal.get("source_action") or "").strip()
            effect = str(proposal.get("direct_effect") or "").strip()
            direction = str(proposal.get("direction") or "").strip()
            evidence = str(proposal.get("evidence") or "").strip()
            if action != str(target.get("action")) or effect != str(target.get("expected_effect")):
                raise ValueError("acquisition proposal must match the exact missing plan step")
            if direction not in {"toward", "away", "neutral"}:
                raise ValueError("acquisition proposal has invalid direction")
            if not evidence:
                raise ValueError("acquisition proposal requires durable run evidence")
            pairs = proposal.get("scope") or []
            if not isinstance(pairs, list) or any(
                not isinstance(pair, dict) or set(pair) != {"key", "value"} for pair in pairs
            ):
                raise ValueError("acquisition proposal scope must be key/value records")
            scope = {str(pair["key"]): str(pair["value"]) for pair in pairs}
            canonical_scope = json.dumps(scope, sort_keys=True)
            certainty = float(proposal.get("certainty", 0.0))
            if not 0.0 <= certainty <= 1.0:
                raise ValueError("acquisition proposal certainty must be between zero and one")
            # Deliberately do not call the authority bridge here. The loop can validate and
            # persist a submission, but it cannot mint its own trusted acquisition event.
            _ = (run_id, canonical_scope)
        return inserted

    def complete_acquisition(self, cur, acquisition_id: int, result: dict) -> None:
        """Close one attempted rung in the same transaction as run+learning.

        MATCHES EVERY *OPEN* STATE, NOT ONLY 'running'. This read `status='running'`, which is
        right for the normal path — the acquisition IS running when its own cycle completes — and
        wrong for RECOVERY, which is the case that actually breaks.

        FOUND BEHAVIOURALLY, NOT BY READING, and only after the pending_reflection fix. A
        reflect-time RateLimited leaves a run with outcome IS NOT NULL / completed_at IS NULL, and
        Loop.cycle's handler calls reset_acquisition_after_failure(), moving the acquisition
        running -> PENDING. The next cycle's finish_pending_reflection() then legitimately picks it
        up and tries to close it — and this guard refused:

            RuntimeError: acquisition #1 was not running at completion

        So the loop still could not recover from the live BB #2608 state even once the acquisition
        was VISIBLE again. Fixing the SELECT without fixing the UPDATE moved the failure one line
        later; it did not remove it. The two belong together, and a static test of either one alone
        would have reported success.

        The guard is worth keeping — it is what stops a second close of an already 'completed' or
        'skipped' row. It just has to be keyed on OPEN rather than on one particular open state.
        Enumerated positively, as in pending_reflection: a future fifth status is then treated as
        NOT-open and fails loudly instead of being silently swept in as closeable.
        """
        cur.execute(
            "UPDATE plan_acquisition SET status='completed', result=%s::jsonb, completed_at=now() "
            "WHERE acquisition_id=%s AND status IN ('pending','running')",
            (psycopg2.extras.Json(result), acquisition_id),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"acquisition #{acquisition_id} was not open at completion")

    def reset_acquisition_after_failure(self, acquisition_id: int) -> None:
        self._q(
            "UPDATE plan_acquisition SET status='pending' WHERE acquisition_id=%s AND status='running'",
            (acquisition_id,),
        )

    def bind_governance_plan(self, work_id: int, global_plan_id: str, plan: dict) -> None:
        """Bind normalized/legacy plan content to its global identity before authorization."""
        row = self._q(
            "UPDATE work SET plan_id=%s,plan=%s::jsonb WHERE id=%s "
            "AND (plan IS NULL OR plan=%s::jsonb) RETURNING id",
            (global_plan_id, psycopg2.extras.Json(plan), work_id, psycopg2.extras.Json(plan)), one=True)
        if row is None:
            raise ValueError("durable work plan differs from normalized authorization request")

    def record_intent_verification(self, work_id: int, plan_id: str, concerns, plan,
                                   verification) -> None:
        self._q(
            "INSERT INTO plan_intent_lineage "
            "(work_id,plan_id,authoritative_concerns,lineage,verifier_id,valid,reasons) "
            "VALUES (%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb) "
            "ON CONFLICT (work_id) DO UPDATE SET "
            "authoritative_concerns=EXCLUDED.authoritative_concerns,lineage=EXCLUDED.lineage,"
            "verifier_id=EXCLUDED.verifier_id,valid=EXCLUDED.valid,reasons=EXCLUDED.reasons,checked_at=now()",
            (work_id, plan_id, psycopg2.extras.Json(concerns), psycopg2.extras.Json(plan),
             verification.verifier_id, verification.valid,
             psycopg2.extras.Json(list(verification.reasons))),
        )

    def reject_work_intent(self, work_id: int, reasons) -> None:
        self._q(
            "UPDATE work SET status='abandoned', rationale=rationale || %s WHERE id=%s",
            ("\nINTENT LINEAGE REJECTED: " + "; ".join(reasons), work_id),
        )

    def reject_work_conflict(self, work_id: int, reasons) -> None:
        self._q(
            "UPDATE work SET status='abandoned', rationale=rationale || %s WHERE id=%s",
            ("\nHARD PLAN CONTRADICTION: " + "; ".join(reasons), work_id),
        )

    def reject_work_governance(self, work_id: int, reason: str) -> None:
        self._q(
            "UPDATE work SET status='abandoned', approved_at=NULL, rationale=rationale || %s WHERE id=%s",
            ("\nCHECKED GOVERNANCE BLOCK: " + reason, work_id),
        )

    def defer_plan_governance(self, work_id: int, global_plan_id: str,
                              request_digest: str, reason_code: str, detail: str) -> None:
        """Atomically append an audit-only outage receipt and park before any run/ACT."""
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO plan_governance_defer_outbox "
                "(global_plan_id,work_id,request_digest,reason_code,detail) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (global_plan_id) DO NOTHING",
                (global_plan_id, work_id, request_digest, reason_code, detail),
            )
            cur.execute(
                "SELECT work_id,request_digest,reason_code FROM plan_governance_defer_outbox "
                "WHERE global_plan_id=%s", (global_plan_id,))
            existing = cur.fetchone()
            if existing != (work_id, request_digest, reason_code):
                raise ValueError("governance defer replay changed plan identity or content")
            cur.execute(
                "UPDATE work SET status='awaiting_human',requires_human=true,gate_reason=%s WHERE id=%s",
                (f"governance deferred ({reason_code}): {detail}", work_id),
            )

    def record_plan_evaluation(self, cur, run_id: int, work: Work, ev,
                               observed_metrics, step_results) -> None:
        cur.execute(
            "INSERT INTO plan_evaluation "
            "(run_id,work_id,plan_id,goal_satisfied,steps_executed,steps_confirmed,"
            " intent_satisfied,observed_metrics,step_results) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
            (run_id, work.id, work.plan_id or f"legacy-work-{work.id}",
             ev.plan_goal_satisfied, ev.steps_executed, ev.steps_confirmed,
             ev.intent_covered, psycopg2.extras.Json(observed_metrics),
             psycopg2.extras.Json(step_results)),
        )

    def resolve_plan_predictions(self, cur, run_id: int, work: Work, ev, step_results):
        """Resolve immutable predictions and append support/refutation events atomically."""
        from .plan_resolution import resolve_plan_steps

        plan_steps = (work.plan or {}).get("steps") or []
        resolution = resolve_plan_steps(
            plan_steps, step_results,
            goal_satisfied=ev.plan_goal_satisfied,
            intent_satisfied=ev.intent_covered,
        )
        cur.execute(
            "SELECT prediction_id,step_id,edge_id,supplier_principle_id "
            "FROM planning_prediction WHERE work_id=%s", (work.id,)
        )
        predictions = {row[1]: row for row in cur.fetchall()}
        for result in resolution.steps:
            prediction = predictions.get(result.step_id)
            if prediction is None:
                continue
            prediction_id, _, edge_id, principle_id = prediction
            cur.execute(
                "UPDATE planning_prediction SET resolved_run_id=%s,executed=%s,"
                "direct_effect_confirmed=%s,direction_confirmed=%s,outcome_kind=%s,"
                "outcome_evidence=%s,actual_outcome=%s,surprise_level=%s,resolved_at=now() "
                "WHERE prediction_id=%s",
                (run_id, result.executed, result.direct_effect_confirmed,
                 result.direction_confirmed, result.outcome_kind, result.evidence,
                 result.outcome_kind, 0.0 if result.direction_confirmed else (1.0 if result.executed else None),
                 prediction_id),
            )
            if not result.executed:
                continue
            delta = 1 if result.direction_confirmed else -1
            cur.execute(
                "INSERT INTO principle_support_event "
                "(prediction_id,run_id,edge_id,principle_id,delta,outcome_kind,evidence) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (prediction_id,run_id) DO NOTHING",
                (prediction_id, run_id, edge_id, principle_id, delta,
                 result.outcome_kind, result.evidence),
            )
            if cur.rowcount:
                # The loop-owned resolution/support rows are an observation outbox, not
                # authority. A separately credentialed governance writer attests and applies the
                # immutable resolution event; the loop never mutates causal_edge here.
                if principle_id:
                    column = "evidence_for_count" if delta > 0 else "evidence_against_count"
                    # Evidence changes counts only. DR12: status/is_active are never promoted here.
                    cur.execute(
                        f"UPDATE causal_principle SET {column}={column}+1,updated_on=now(),"
                        "updated_by='live-plan-resolution-v1' WHERE principle_id=%s",
                        (principle_id,),
                    )
        if resolution.failed_step_id:
            cur.execute(
                "INSERT INTO plan_replan "
                "(source_work_id,source_plan_id,failed_step_id,preserved_prefix_step_ids,reason) "
                "VALUES (%s,%s,%s,%s::jsonb,%s) "
                "ON CONFLICT (source_work_id,failed_step_id) DO NOTHING",
                (work.id, work.plan_id or f"legacy-work-{work.id}", resolution.failed_step_id,
                 psycopg2.extras.Json(list(resolution.preserved_prefix_step_ids)),
                 resolution.failure_reason or "direction_refuted"),
            )
        return resolution

    def pending_replans(self, limit: int = 10):
        return self._q(
            "SELECT replan_id,source_work_id,source_plan_id,failed_step_id,"
            "preserved_prefix_step_ids,reason FROM plan_replan "
            "WHERE status='pending' ORDER BY created_at LIMIT %s", (limit,)
        )

    def link_pending_replan(self, replacement_work_id: int, replacement_plan_id: str) -> None:
        self._q(
            "UPDATE plan_replan SET status='planned',replacement_work_id=%s,replacement_plan_id=%s "
            "WHERE replan_id=(SELECT replan_id FROM plan_replan WHERE status='pending' "
            "ORDER BY created_at LIMIT 1)",
            (replacement_work_id, replacement_plan_id),
        )

    def approve_work(self, work_id: int):
        """Approve one parked work item and return the validated row, or None on conflict."""
        row = self._q(
            "UPDATE work SET status='pending', approved_at=now() "
            "WHERE id=%s AND status='awaiting_human' "
            "RETURNING *",
            (work_id,),
            one=True,
        )
        if not row:
            return None
        assert_valid_approval(row)
        return row

    def start_run(self, work_id: int, plan_authorization_id: str | None = None) -> int:
        with self.tx() as cur:
            cur.execute("UPDATE work SET status='running' WHERE id=%s", (work_id,))
            cur.execute("INSERT INTO runs (work_id,plan_authorization_id) VALUES (%s,%s) RETURNING id",
                        (work_id, plan_authorization_id))
            return int(cur.fetchone()[0])

    def complete_run(self, cur, run_id: int, outcome: str, succeeded: bool, usage,
                     productive: bool = True, evidence: str = "",
                     measure_before=None, measure_after=None, escalation_level: str = "autonomous",
                     work_status: str = "done") -> None:
        cur.execute(
            "UPDATE runs SET completed_at=now(), outcome=%s, succeeded=%s, "
            "cost_usd=%s, tokens_in=%s, tokens_out=%s, "
            "productive=%s, evidence=%s, measure_before=%s, measure_after=%s, escalation_level=%s "
            "WHERE id=%s",
            (outcome, succeeded, usage.cost_usd, usage.tokens_in, usage.tokens_out,
             productive, evidence, measure_before, measure_after, escalation_level, run_id),
        )
        if work_status not in {"done", "pending"}:
            raise ValueError(f"invalid completed-run work status {work_status!r}")
        cur.execute("UPDATE work SET status=%s WHERE id=(SELECT work_id FROM runs WHERE id=%s)",
                    (work_status, run_id))
        # Mandatory event: a deferred constraint rejects any completed checked run without it.
        cur.execute(
            "INSERT INTO plan_outcome_evaluation_outbox(run_id) "
            "SELECT id FROM runs WHERE id=%s AND plan_authorization_id IS NOT NULL "
            "ON CONFLICT(run_id) DO NOTHING", (run_id,))

    def write_learning(self, cur, run_id, insight, evidence, scope, confidence,
                       evidence_kind: str = "actor_claim") -> int:
        if evidence_kind not in {"actor_claim", "verified_evidence"}:
            raise ValueError(f"invalid evidence kind {evidence_kind!r}")
        confidence = float(confidence)
        if evidence_kind == "actor_claim":
            confidence = min(confidence, 0.99)
        cur.execute(
            "INSERT INTO learnings (run_id, insight, evidence, scope, confidence, evidence_kind) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (run_id, insight, evidence, scope, confidence, evidence_kind),
        )
        return cur.fetchone()[0]

    def recent_productivity(self, limit: int = 15):
        """Newest first. The escalation ladder reads THIS — the database — not a counter in
        memory. A counter that resets on restart lets a stuck loop hammer forever by crashing."""
        return self._q(
            "SELECT r.id, coalesce(r.productive, false) AS productive, r.completed_at "
            "FROM runs r JOIN work w ON w.id = r.work_id "
            "WHERE r.completed_at IS NOT NULL AND w.kind <> 'curiosity' "
            "ORDER BY r.completed_at DESC LIMIT %s", (limit,))

    def fail_run(self, run_id: int, error: str) -> None:
        """A cycle that died leaves a row saying it died. Loud, not silent."""
        self._q(
            "UPDATE runs SET completed_at=now(), outcome=%s, succeeded=false, error=%s WHERE id=%s",
            (f"FAILED: {error[:400]}", error, run_id),
        )
        self._q("UPDATE work SET status='pending' WHERE id=(SELECT work_id FROM runs WHERE id=%s)", (run_id,))

    def quarantine_acquisition_receipt(self, run_id: int, acquisition_id: int | None,
                                       error: str, decision_id: int | None) -> None:
        """Persist a side-effect/receipt violation and require human review; never auto-retry."""
        warning = f"{APPROVED_SIDE_EFFECTS_WARNING} Acquisition receipt violation: {error[:500]}"
        with self.tx() as cur:
            cur.execute(
                "UPDATE runs SET completed_at=now(),succeeded=false,error=%s WHERE id=%s",
                (warning, run_id))
            cur.execute(
                "UPDATE work SET status='awaiting_human',approved_at=NULL, "
                "rationale=CASE WHEN rationale LIKE %s THEN rationale ELSE rationale || %s END "
                "WHERE id=(SELECT work_id FROM runs WHERE id=%s)",
                (f"%{warning}%", f"\n\n{warning}", run_id))
            if acquisition_id is not None:
                cur.execute(
                    "UPDATE plan_acquisition SET status='skipped',completed_at=now(), "
                    "result=jsonb_build_object('receipt_violation',%s) WHERE acquisition_id=%s",
                    (error[:1000], acquisition_id))
            if decision_id is not None:
                cur.execute(
                    "UPDATE meta_mode_spend_reservation SET status='incurred',incurred_at=now() "
                    "WHERE decision_id=%s AND status='reserved'", (decision_id,))

    def fail_approved_run(self, run_id: int, error: str) -> None:
        """Approved work failed after the human released it. Do not auto-retry it.

        A failed sensitive action must go back to the human with a fresh approval requirement,
        not stay `pending + approved_at` and fire forever.
        """
        self._q(
            "UPDATE runs SET completed_at=now(), outcome=%s, succeeded=false, error=%s WHERE id=%s",
            (
                f"FAILED APPROVED WORK: {error[:300]}. {APPROVED_SIDE_EFFECTS_WARNING}",
                f"{error}. {APPROVED_SIDE_EFFECTS_WARNING}",
                run_id,
            ),
        )
        self._q(
            "UPDATE work SET status='awaiting_human', approved_at=NULL, "
            "rationale = CASE WHEN rationale LIKE %s THEN rationale ELSE rationale || %s END "
            "WHERE id=(SELECT work_id FROM runs WHERE id=%s)",
            (f"%{APPROVED_SIDE_EFFECTS_WARNING}%", f"\n\n{APPROVED_SIDE_EFFECTS_WARNING}", run_id),
        )

    def interrupt_approved_after_act(self, run_id: int, reason: str) -> None:
        """Approved work acted, then reflection failed. Finish the learning; never re-act.

        The run stays incomplete with its outcome, so finish_pending_reflection() can complete the
        cycle. The work is re-parked with approval cleared and an explicit warning in case a human
        sees it before the next cycle finishes the reflection.
        """
        self._q(
            "UPDATE runs SET error=%s WHERE id=%s AND completed_at IS NULL",
            (f"INTERRUPTED AFTER APPROVED ACT: {reason}. {APPROVED_SIDE_EFFECTS_WARNING}", run_id),
        )
        self._q(
            "UPDATE work SET status='awaiting_human', approved_at=NULL, "
            "rationale = CASE WHEN rationale LIKE %s THEN rationale ELSE rationale || %s END "
            "WHERE id=(SELECT work_id FROM runs WHERE id=%s)",
            (f"%{APPROVED_SIDE_EFFECTS_WARNING}%", f"\n\n{APPROVED_SIDE_EFFECTS_WARNING}", run_id),
        )

    def record_act(self, run_id: int, outcome: str, succeeded: bool, evidence: str) -> None:
        """The act HAPPENED. Write it down NOW, before the learning.

        Because the act has already changed the world, and if we lose the record of it the next
        cycle will cheerfully do it AGAIN. completed_at stays NULL: the cycle is not finished
        until it has LEARNED, and verify.sh still (correctly) refuses to count it.
        """
        self._q("UPDATE runs SET outcome=%s, succeeded=%s, evidence=%s WHERE id=%s",
                (outcome, succeeded, evidence, run_id))

    def orphaned_runs(self):
        """Runs that STARTED and died before recording ANYTHING — completed_at NULL and outcome
        NULL. A hard kill (Ctrl-C, terminal close, reboot, OOM) between start_run() and the first
        record leaves exactly this: a zombie run + work stuck in 'running', with no reconciler.

        Distinct from pending_reflection() (outcome SET, learning missing). error IS NULL excludes
        rows already reconciled, so we never process the same zombie twice."""
        return self._q(
            "SELECT id, work_id FROM runs "
            "WHERE completed_at IS NULL AND outcome IS NULL AND error IS NULL")

    def pending_reflection(self):
        """A run that ACTED but never LEARNED — because we were rate-limited or crashed between
        the two. It must be finished, not repeated: the work is already done out in the world.

        L1 FIX — THE JOIN MUST MATCH EVERY *OPEN* ACQUISITION, NOT ONLY A RUNNING ONE.

        This read `pa.status='running'`. plan_acquisition.status DEFAULTS TO 'pending' (see
        schema/011_plan_acquisition.sql), so a freshly created acquisition was INVISIBLE to this
        join. The recovery path then saw acquisition_id=NULL, concluded the row was ordinary work,
        and tried to mark it done — which the database correctly refused:

            work 1 cannot be done while acquisition is open

        and the loop stopped turning on restart. A live container reproduced exactly that: work
        RUNNING, run acted-but-incomplete, acquisition PENDING, zero edges (independent review
        BB #2608).

        The status vocabulary is fixed by a CHECK constraint: pending, running, completed, skipped.
        OPEN is the first two. Enumerate the open states rather than excluding the terminal ones —
        if a fifth status is ever added it will be treated as NOT-open and surface as a loud
        recovery failure, instead of being silently swept in as open.
        """
        return self._q(
            "SELECT r.id, r.work_id, r.outcome, r.succeeded, r.evidence, w.summary, w.rationale, "
            "pa.acquisition_id "
            "FROM runs r JOIN work w ON w.id = r.work_id "
            "LEFT JOIN plan_acquisition pa ON pa.work_id=w.id "
            "AND pa.status IN ('pending','running') "
            "WHERE r.completed_at IS NULL AND r.outcome IS NOT NULL "
            "ORDER BY r.started_at LIMIT 1", one=True)

    def interrupt_run(self, run_id: int, reason: str) -> None:
        """The act was cut short (rate limit) and WE DO NOT KNOW WHETHER IT DID ANYTHING.

        We used to DELETE the run here. On a real box that produced a database with a model row in
        it and NO RUN EXPLAINING WHERE IT CAME FROM — the act had already written to the world
        before the plan ran out, and deleting the run erased the only evidence.

        You cannot know, after the fact, whether an interrupted act had side effects. So never
        assert that it didn't. Keep the row, say it was interrupted, and let the NEXT cycle look at
        the world (it re-reads the mission's number every time) before deciding to repeat anything.
        """
        self._q("UPDATE runs SET error=%s WHERE id=%s AND completed_at IS NULL",
                (f"INTERRUPTED: {reason}. Side effects UNKNOWN — verify before repeating.", run_id))
        self._q("UPDATE work SET status='pending' WHERE id=(SELECT work_id FROM runs WHERE id=%s)",
                (run_id,))

    def interrupt_approved_run(self, run_id: int, reason: str) -> None:
        """Rate-limited approved work is bounded: it requires a fresh human release to retry."""
        self._q(
            "UPDATE runs SET completed_at=now(), outcome=%s, succeeded=false, error=%s "
            "WHERE id=%s AND completed_at IS NULL",
            (
                f"INTERRUPTED APPROVED WORK: {reason[:280]}. {APPROVED_SIDE_EFFECTS_WARNING}",
                f"INTERRUPTED APPROVED WORK: {reason}. Cleared approval so it cannot retry "
                f"without a fresh human approval. {APPROVED_SIDE_EFFECTS_WARNING}",
                run_id,
            ),
        )
        self._q(
            "UPDATE work SET status='awaiting_human', approved_at=NULL, "
            "rationale = CASE WHEN rationale LIKE %s THEN rationale ELSE rationale || %s END "
            "WHERE id=(SELECT work_id FROM runs WHERE id=%s)",
            (f"%{APPROVED_SIDE_EFFECTS_WARNING}%", f"\n\n{APPROVED_SIDE_EFFECTS_WARNING}", run_id),
        )

    def abandon_run(self, run_id: int) -> None:
        """Rate-limited BEFORE the act did anything. Nothing happened, so nothing is recorded.

        We DELETE the run rather than marking it failed: a rate limit is not an outcome, and
        recording it as one would poison the history the loop learns from — the next cycle would
        "learn" from a failure that never happened.

        BUT ONLY IF THE ACT NEVER RAN. If outcome is set, the act already changed the world, and
        deleting the run would erase the only evidence that the work was done — so the next cycle
        would do it AGAIN. Found by a clean-room run: the plan rate-limited during REFLECT, the
        run was deleted, and the database ended up with 10 rows of real work and ZERO runs
        explaining where they came from.
        """
        r = self._q("SELECT outcome FROM runs WHERE id=%s", (run_id,), one=True)
        if r and r["outcome"]:
            log.warning("run #%s ACTED already — keeping it. It needs a learning, not a redo.", run_id)
            return
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

    def park_for_human(self, work_id: int, note: str | None = None) -> None:
        """Park before ACT and persist the exact consequence-based reason separately."""
        self._q(
            "UPDATE work SET status='awaiting_human', requires_human=true, gate_reason=%s "
            "WHERE id=%s",
            (note, work_id),
        )

    def reject_work(self, work_id: int):
        return self._q(
            "UPDATE work SET status='abandoned', approved_at=NULL "
            "WHERE id=%s AND status='awaiting_human' RETURNING *",
            (work_id,), one=True,
        )

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
        # public FIRST — see schema/001_init.sql. ag_catalog first would silently redirect any
        # CREATE in this session into AGE's schema. cypher() is schema-qualified below, so
        # ag_catalog does not need to lead.
        cur.execute("LOAD 'age'; SET search_path = public, ag_catalog, \"$user\";")
        cur.execute(
            "SELECT * FROM ag_catalog.cypher('autonomy_quest', $$ "
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

    def read_frontier(self, source: str, limit: int = 1):
        """Read curiosity's externally seeded frontier.

        The query is configured by the human/operator and validated for forbidden loop-owned
        sources before this point. Runtime still reads the real source each time.
        """
        rows = self._q(source)
        return rows[:limit]

    def count_frontier(self, source: str) -> Decimal:
        """Count the externally seeded frontier without assuming its first column is numeric."""
        return Decimal(len(self._q(source)))

    def curiosity_promoted_recent(self, window_cycles: int) -> int:
        row = self._q(
            """SELECT COUNT(*) AS n
               FROM shared_learnings
               WHERE origin_instance='local-curiosity'
                 AND status='promoted'
                 AND received_at >= (
                   SELECT COALESCE(MIN(started_at), now())
                   FROM (
                     SELECT r.started_at
                     FROM runs r JOIN work w ON w.id = r.work_id
                     WHERE w.kind='curiosity' AND r.completed_at IS NOT NULL
                     ORDER BY r.started_at DESC
                     LIMIT %s
                   ) recent
                 )""",
            (window_cycles,), one=True)
        return int(row["n"])

    def curiosity_cycles_recent(self, window_cycles: int) -> int:
        row = self._q(
            """SELECT COUNT(*) AS n
               FROM (
                 SELECT r.id
                 FROM runs r JOIN work w ON w.id = r.work_id
                 WHERE w.kind='curiosity' AND r.completed_at IS NOT NULL
                 ORDER BY r.started_at DESC
                 LIMIT %s
               ) recent""",
            (window_cycles,), one=True)
        return int(row["n"])

    def stage_curiosity_learning(self, *, run_id: int, insight: str, confidence: float, evidence: str,
                                 outcome: str, applies_when: str, falsified_by: str) -> int | None:
        """Stage curiosity output as inert shared learning.

        It deliberately does not write to learnings. Promotion through the existing adjudication
        path is the only way this can ever reach live_learnings().
        """
        return self.stage_import(
            origin_instance="local-curiosity",
            origin_mission="local bounded exploration",
            origin_run_id=run_id,
            origin_evidence=evidence,
            origin_outcome=outcome,
            insight=insight,
            confidence=confidence,
            applies_when=applies_when,
            falsified_by=falsified_by,
            imported_by="curiosity",
        )

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
            """INSERT INTO learnings (run_id, insight, evidence, scope, confidence, evidence_kind)
               SELECT id, %s, %s, 'generalisable', least(%s, 0.99), 'actor_claim'
               FROM runs ORDER BY id DESC LIMIT 1
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
