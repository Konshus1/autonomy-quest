"""The loop. This file is the product; everything else in the repo exists to keep it turning.

    observe -> decide -> act -> record -> learn -> (back to observe)

It runs through an EXECUTOR, which is either the human's TUI agent under their subscription
(flat rate, search included) or a metered model API. The loop does not know or care which.

Three invariants are enforced here in code, not in prose, because prose does not stop a running
system from doing the wrong thing:

  1. A cycle is not complete until it has LEARNED. The learning row is written in the same
     transaction as the run's completion. There is no code path that finishes a run and skips
     the learning — that path is what would turn this into a cron job, and verify.sh gates on
     exactly the join it would break.

  2. The system NEVER grades its own homework. "Did the number move?" is answered by re-reading
     the mission's measure from its real source, not by asking the model how it thinks it did.
     A loop that scores itself optimises for feeling successful.

  3. The autonomy gate fires BEFORE the act, not as an audit after it. A boundary you check
     afterwards is not a boundary; it's a regret.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal

from . import causal_sync, consult_act, merge_sync, prompts
from .budget import Budget, BudgetExceeded
from .config import Instance
from .evaluate import Evaluator
from .curiosity import (
    CuriosityConfigError,
    exploration_measure,
    should_explore,
    validate_config,
)
from .db import APPROVED_SIDE_EFFECTS_WARNING, Db, Work
from .escalation import Escalation, Hibernate
from .executor import AgentFailed, RateLimited, Usage
from .approval import assert_valid_approval

log = logging.getLogger("aq.loop")

# Module global: set by Loop.__init__ so the management API can access the
# executor for T10 LLM classification (Option C, Kevin BB #856) when running
# in the same process.
_active_executor = None


@dataclass
class Cycle:
    run_id: int
    work_id: int
    learned: str


def _total(*usages) -> Usage:
    """Sum the cost of every model call made during one cycle."""
    return Usage(
        tokens_in=sum(u.tokens_in for u in usages),
        tokens_out=sum(u.tokens_out for u in usages),
        cost_usd=sum((u.cost_usd for u in usages), start=Decimal("0")),
    )


class Loop:
    def __init__(self, inst: Instance, db: Db, executor) -> None:
        self.inst = inst
        self.db = db
        self.ex = executor
        self.budget = Budget(inst.budget, db)
        self.esc = Escalation(db)
        self.evaluator = Evaluator(self.esc)
        validate_config(inst.curiosity)
        # Expose the executor to the management API for T10 LLM classification
        # (Option C per Kevin BB #856). When the loop and management API run in
        # the same process (the standard AQ deployment), the API can access the
        # executor directly via the module global.
        import runner.loop as _loop_mod
        _loop_mod._active_executor = executor
        os.environ["AQ_EXECUTOR_AVAILABLE"] = "1"
        os.environ["AQ_EXECUTOR_TYPE"] = type(executor).__name__

    # -- one full turn ------------------------------------------------------
    def cycle(self) -> Cycle | None:
        """Run exactly one cycle. Returns None if there was nothing to do.

        Raises BudgetExceeded if the hard cap is hit; we do NOT catch it and soldier on. A hard
        cap that can be argued past is not a cap, and "just this once" is how an autonomous
        system quietly spends someone's month.
        """
        self.budget.check_hard_cap()

        # RECONCILE ZOMBIES FIRST. A run that started and died before recording ANYTHING (a hard
        # kill: Ctrl-C, terminal close, reboot, OOM, between start_run and the first record) leaves
        # a NULL run + work stuck 'running'. Nothing else cleans it up — finish_pending_reflection
        # only handles acted-but-not-learned. Same family as reboot survival: a process can die at
        # any point and the system must recover honestly rather than leave a zombie.
        self.reconcile_orphaned_runs()

        # An earlier cycle ACTED but never LEARNED — rate-limited or crashed in between. FINISH
        # IT. Do not start new work on top of work whose outcome was never recorded: the act
        # already happened out in the world, and repeating it would double it.
        self.finish_pending_reflection()

        world = self.observe()
        measure_before = world["now"]

        # OVERSHOOT TRIPWIRE — the guardian that did NOT fire at 50,082.
        #
        # A reach-and-maintain measure that has blown WAY past its target is a measure being GAMED
        # by volume, not a mission being served. The cost-tripwire (artifact-or-escalate) never
        # caught this because every cycle WAS producing artifacts — just 800x too many. "Did it
        # produce something" is necessary but not sufficient; "is the something still SERVING the
        # goal" is the missing check. So: if we are massively over target, STOP and get a human,
        # via the same hibernation machinery as any other stop (notify once, survive restart,
        # resume only on a real signal).
        if world.get("overshooting"):
            raise Hibernate(
                f"MEASURE OVERSHOOT: {self.inst.mission.measure.what} is at {world['now']}, target "
                f"is {world.get('target')}. A reach-and-maintain mission does not run "
                f"its number this far past target — the measure is being satisfied by VOLUME. "
                f"Halting rather than burning more on a runaway. A human should check whether the "
                f"measure needs a DISTINCT/ceiling, or the data needs truncating, before resuming."
            )

        approved = self.db.approved_work()
        if approved:
            approval = assert_valid_approval(approved)
            work = Work(
                id=approval.work_id,
                kind=approved["kind"],
                summary=approved["summary"],
                rationale=approved["rationale"],
                requires_human=approved["requires_human"],
            )
            log.warning("executing human-approved parked work #%s — %s", work.id, work.summary)
            return self.execute_work(
                work,
                measure_before=measure_before,
                decision_usage=Usage(),
                escalation_level="approved",
                approved_row=approved,
            )

        self.maybe_explore(world)

        # THE LADDER. If recent cycles produced nothing, say so LOUDLY in the prompt — a stuck
        # loop that is not told it is stuck will try the same thing again with more determination.
        verdict = self.esc.assess(self.esc.unproductive_streak())   # raises Hibernate at the floor
        if verdict.level != "autonomous":
            log.warning("escalation: %s (%d unproductive cycles)", verdict.level, verdict.unproductive)
        if verdict.notify_human:
            self.notify_human_stuck(verdict, world)

        decision, u_decide = self.ex.run(
            prompts.decide(world, self.inst.template, guidance=verdict.guidance),
            prompts.DECIDE_SCHEMA, tier="reasoning"
        )
        if decision.get("do_nothing"):
            log.info("nothing worth doing this cycle — not inventing busywork")
            return None

        work = Work(
            id=0,
            kind=decision["kind"],
            summary=decision["summary"],
            rationale=decision["rationale"],
            reversible=decision["reversible"],
            spends_money=decision["spends_money"],
            touches_human=decision["touches_human"],
            commits=decision["commits"],
        )

        # Persist the decision BEFORE anything happens to it. If we park it or crash mid-act,
        # there must still be a row saying what we decided and why. A decision that exists only
        # in memory is a decision nobody can audit.
        work.id = self.db.create_work(
            kind=work.kind, summary=work.summary, rationale=work.rationale,
            requires_human=work.requires_human,
        )

        # CONSULT-ACT (BB #746, Slice 3): a mined principle's certainty now INFLUENCES the
        # gate — not just scores the plan. Strictly one-directional: it can only ADD a reason
        # to defer a low-certainty, side-effecting step to review; it can never make the gate
        # more permissive (it is OR-ed into requires_human, never used to skip it). Read-only
        # and best-effort — any consult failure degrades to "no opinion", never blocks the loop.
        consult_act_defer = False
        consult_act_note = None
        try:
            ca_base = causal_sync.mgmt_base_url()
            if ca_base:
                ca_certainty = causal_sync.assess_plan_certainty(ca_base, work.kind, "measure_up")
                consult_act_defer = consult_act.should_defer(work, ca_certainty)
                consult_act_note = consult_act.gate_reason(work, ca_certainty)
        except Exception:  # pragma: no cover - belt-and-suspenders around a read-only consult
            log.debug("consult-act skipped (non-fatal)", exc_info=True)
            consult_act_defer = False

        # INVARIANT 3 — the gate fires BEFORE the act.
        if self.requires_human(work) or consult_act_defer:
            # consult_act_note is None for a base-gate park; when set, it is persisted on the row
            # AND surfaced in the notification so the deferral's reason reaches the reviewer.
            self.db.park_for_human(work.id, note=consult_act_note)
            self.notify_human(work, reason=consult_act_note)
            if consult_act_defer:
                log.info("work #%s deferred by consult-act — %s", work.id, consult_act_note)
            else:
                log.info("work #%s needs you — parked and notified, NOT executed", work.id)
            return None

        return self.execute_work(
            work,
            measure_before=measure_before,
            decision_usage=u_decide,
            escalation_level=verdict.level,
        )

    def execute_work(self, work: Work, *, measure_before, decision_usage: Usage,
                     escalation_level: str = "autonomous", approved_row=None) -> Cycle:
        """Execute one already-authorized work item through act -> record -> learn.

        Autonomous decisions and human-approved parked work both pass through this single path.
        Approved parked work is validated before the first side-effecting act; the validation is
        consequence-free and shared with /api/approve.
        """
        if approved_row is not None:
            assert_valid_approval(approved_row)

        # CONSULT (read-only, BB #746): what certainty do the mined causal principles give this
        # action's effect? Recorded BEFORE the act as an honest prediction — it SCORES the plan,
        # it does not choose the work (that stays roadmap until formal principles + promotion land).
        # None when no principle governs this step yet, or the management API is unreachable.
        # HARD RULE: this is a pre-act, side-effect-free observation — it must NEVER be able to stop
        # the act. So the whole consult is wrapped: any failure degrades to "no prediction", never
        # an exception into the cycle.
        causal_base = predicted_certainty = None
        try:
            causal_base = causal_sync.mgmt_base_url()
            if causal_base:
                predicted_certainty = causal_sync.assess_plan_certainty(
                    causal_base, work.kind, "measure_up")
        except Exception:  # pragma: no cover - belt-and-suspenders around a read-only consult
            log.debug("causal consult skipped (non-fatal)", exc_info=True)
            causal_base = predicted_certainty = None

        run_id = self.db.start_run(work.id)
        acted = False
        try:
            result, u_act = self.ex.run(
                prompts.act(work, self.inst.mission.boundaries), prompts.ACT_SCHEMA, tier="working"
            )
            outcome, succeeded = result["outcome"], result["succeeded"]

            # WRITE THE ACT DOWN NOW. It already changed the world. If we are rate-limited or
            # crash during reflect, this record is what stops the next cycle repeating the work.
            self.db.record_act(run_id, outcome, succeeded, result.get("evidence", ""))
            acted = True

            insight, u_learn = self.ex.run(
                prompts.reflect(work, outcome, succeeded, self.db.live_learnings(limit=50)),
                prompts.REFLECT_SCHEMA, tier="reasoning",
            )
        except RateLimited as e:
            # Expected on a subscription — the plan is used up. NOT a failure.
            #
            # But do NOT delete the run: the act may ALREADY have changed the world before the
            # plan ran out. On a real box that produced a model row with no run explaining it.
            # You cannot know afterwards whether an interrupted act had side effects, so never
            # assert that it didn't.
            if approved_row is not None and acted:
                self.db.interrupt_approved_after_act(run_id, str(e)[:120])
                self.notify_human(self._work_with_approval_warning(work))
            elif approved_row is not None:
                self.db.interrupt_approved_run(run_id, str(e)[:120])
                self.notify_human(self._work_with_approval_warning(work))
            else:
                self.db.interrupt_run(run_id, str(e)[:120])
            raise
        except Exception as e:
            # Fail LOUD. A cycle that dies leaves a row saying it died. A silent failure here is
            # a system that looks alive and is not.
            if approved_row is not None and acted:
                self.db.interrupt_approved_after_act(run_id, str(e)[:120])
                self.notify_human(self._work_with_approval_warning(work))
            elif approved_row is not None:
                self.db.fail_approved_run(run_id, error=str(e))
                self.notify_human(self._work_with_approval_warning(work))
            else:
                self.db.fail_run(run_id, error=str(e))
            log.exception("cycle failed on work #%s — recorded, not swallowed", work.id)
            raise

        # EVERY call in the cycle is charged to the run — the deciding and the reflecting, not
        # just the doing. Charging only the visible work understates the budget, and the budget
        # is the one number the human is trusting us to keep honest. (On subscription mode these
        # are all zero, which is the point.)
        usage = _total(decision_usage, u_act, u_learn)

        # ARTIFACT-OR-ESCALATE. Did this cycle actually DO something, or did it narrate?
        # We do NOT ask the agent whether it was productive — it would say yes. We re-read the
        # mission's number and check for pointable evidence.
        measure_after = self.db.read_measure(self.inst.mission.measure)
        # STAGE 7 — Evaluate (EVAL_MERGE_SPEC.md). Returns was_productive's boolean UNCHANGED (the
        # ladder's input) plus a richer verdict that drives the manager-gated merge step below.
        ev = self.evaluator.evaluate(
            work=work, outcome=outcome, succeeded=succeeded, evidence=result.get("evidence", ""),
            measure_before=measure_before, measure_after=measure_after,
            insight=insight, predicted_certainty=predicted_certainty)
        productive = ev.productive
        if not productive:
            log.warning("cycle produced no artifact and moved no number — this counts toward escalation")

        # INVARIANT 1 — record and learn are ONE transaction. There is no path that completes a
        # run and skips the learning, because they are the same commit.
        with self.db.tx() as tx:
            self.db.complete_run(
                tx, run_id, outcome=outcome, succeeded=succeeded, usage=usage,
                productive=productive, evidence=result.get("evidence", ""),
                measure_before=measure_before, measure_after=measure_after,
                escalation_level=escalation_level)
            learning_id = self.db.write_learning(
                tx, run_id=run_id,
                insight=insight["insight"], evidence=insight["evidence"],
                scope=insight["scope"], confidence=insight["confidence"],
            )
            self.db.graph_link(tx, run_id=run_id, work_id=work.id, learning_id=learning_id)

        self.db.beat("turning", f"run #{run_id} complete")

        # CLOSE THE CAUSAL LOOP (BB #746/#764) — POST-COMMIT and BEST-EFFORT. The run is already
        # durably recorded; a causal-scoring hiccup (API down, malformed response, blip) must never
        # fail it, drop the returned Cycle, or skip the soft-cap check below. The whole block is
        # wrapped so even a contract-drifted response body cannot escape into the cycle.
        if causal_base:
            try:
                # (1) SCORE the pre-act prediction against reality: a surprise on the governing
                #     edge earns support (confirm) or proposes a gated demotion — how the learning
                #     loop feeds back into the principles from REAL operation. Fires only when a
                #     principle governed this step (predicted_certainty is not None).
                if predicted_certainty is not None:
                    s = causal_sync.record_outcome_surprise(
                        causal_base, work.kind, "measure_up",
                        predicted_certainty, actual_success=(measure_after > measure_before))
                    surprise = s.get("surprise") if isinstance(s, dict) else None
                    if isinstance(surprise, dict):
                        log.info("causal outcome scored — %s (predicted %.2f) on %s->measure_up",
                                 surprise.get("signal"), predicted_certainty, work.kind)
                # (2) MINE fresh principles from this productive run so new edges appear.
                if productive:
                    mined = causal_sync.refresh_causal_principles(causal_base)
                    if mined is not None:
                        log.info("causal principles refreshed — %d edge(s) after run #%s",
                                 mined, run_id)

                # (3) T11 FRAME EXPANSION (C10/DR5) — feed this cycle's learning as an
                #     episode into the frame-expansion pipeline. If the system's dimension
                #     library can't describe something it just learned, mapping_exhausted
                #     fires and a candidate dimension is proposed (status="proposed", NO
                #     auto-promotion). This is the C10 capability running live.
                #     Best-effort: never affects the already-recorded cycle.
                try:
                    causal_sync.feed_frame_expansion(
                        causal_base,
                        work.kind,
                        work.summary,
                        insight["insight"],
                        outcome,
                        succeeded,
                    )
                except Exception:  # pragma: no cover - T11 is best-effort
                    log.debug("T11 frame expansion skipped (non-fatal)", exc_info=True)
            except Exception:  # pragma: no cover - never let scoring undo a recorded cycle
                log.debug("causal post-commit scoring skipped (non-fatal)", exc_info=True)

        # STAGE 8 — manager-gated merge decision as a LIVE loop step (EVAL_MERGE_SPEC.md).
        # POST-COMMIT + BEST-EFFORT (mirrors the causal hook): the run is durable; a merge-API
        # hiccup must never fail the cycle. A 'rework' verdict emits NOTHING — unmerged work is
        # the loop continuing to climb the escalation ladder. Cohort-of-one for this first slice.
        if ev.verdict in ("pass", "escalate"):
            try:
                base = causal_sync.mgmt_base_url()
                if base:
                    packet = merge_sync.build_merge_packet(
                        ev=ev, run_id=run_id, work=work,
                        manager_handle=os.environ.get("AQ_MANAGER_HANDLE", "ralph-manager"),
                        cohort_id=f"cohort-run-{run_id}")
                    r = merge_sync.submit_merge_decision(base, packet)
                    if isinstance(r, dict) and r.get("ok"):
                        log.info("merge decision emitted: %s for %s",
                                 packet["decision"], packet["cohort_id"])
            except Exception:  # pragma: no cover - a merge hiccup must never undo a recorded cycle
                log.debug("merge-decision emit skipped (non-fatal)", exc_info=True)

        self.budget.check_soft_cap()
        log.info("cycle complete — run #%s, cost $%s, learned: %s",
                 run_id, usage.cost_usd, insight["insight"][:80])
        return Cycle(run_id=run_id, work_id=work.id, learned=insight["insight"])

    @staticmethod
    def _work_with_approval_warning(work: Work) -> Work:
        rationale = work.rationale
        if APPROVED_SIDE_EFFECTS_WARNING not in rationale:
            rationale = f"{rationale}\n\n{APPROVED_SIDE_EFFECTS_WARNING}"
        return Work(
            id=work.id,
            kind=work.kind,
            summary=work.summary,
            rationale=rationale,
            requires_human=work.requires_human,
            reversible=work.reversible,
            spends_money=work.spends_money,
            touches_human=work.touches_human,
            commits=work.commits,
        )

    def maybe_explore(self, world: dict) -> None:
        """Run one optional curiosity cycle if its bounded appetite permits.

        Curiosity is cut before mission work. It records cost in runs, but its output is staged
        inertly in shared_learnings and cannot influence decisions until review/promote.
        """
        c = self.inst.curiosity
        if not c.enabled:
            return

        try:
            frontier_measure = exploration_measure(c)
            frontier_now = self.db.count_frontier(frontier_measure.where)
            frontier_target = self.db.read_scalar(c.frontier.target_query) if c.frontier.target_query else (
                Decimal(str(c.frontier.target)) if c.frontier.target is not None else None)
            decision = should_explore(
                c,
                spent_today=world["spent_today"],
                mission_soft_cap_reached=self.budget.soft_cap_reached(world["spent_today"]),
                curiosity_cycles_today=self.db.curiosity_cycles_today(),
                curiosity_cost_today=self.db.curiosity_cost_today(),
                promoted_recent=self.db.curiosity_promoted_recent(c.ratchet.window_cycles),
                cycles_recent=self.db.curiosity_cycles_recent(c.ratchet.window_cycles),
                frontier_current=frontier_now,
                frontier_target=frontier_target,
            )
        except CuriosityConfigError:
            raise
        except Exception:
            log.exception("curiosity check failed; skipping optional exploration")
            return

        if not decision.run:
            if "overshoot" in decision.reason:
                log.warning("curiosity skipped: %s", decision.reason)
            else:
                log.info("curiosity skipped: %s", decision.reason)
            return

        frontier = self.db.read_frontier(c.frontier.source, limit=c.frontier.max_items_per_cycle)
        if not frontier:
            log.info("curiosity skipped: frontier returned no items")
            return

        work_id = self.db.create_work(
            kind="curiosity",
            summary=f"Explore {len(frontier)} frontier item(s)",
            rationale="Optional bounded exploration from a configured external authority",
        )
        run_id = self.db.start_run(work_id)
        try:
            result, usage = self.ex.run(
                prompts.explore(self.inst.mission, frontier, c.frontier.authority),
                prompts.EXPLORE_SCHEMA,
                tier="reasoning",
            )
        except RateLimited as e:
            self.db.interrupt_run(run_id, str(e)[:120])
            raise
        except Exception as e:
            self.db.fail_run(run_id, error=str(e))
            log.exception("curiosity cycle failed on work #%s — recorded, not swallowed", work_id)
            return

        with self.db.tx() as tx:
            self.db.complete_run(
                tx, run_id, outcome=result["outcome"], succeeded=result["succeeded"],
                usage=usage, productive=bool(result["succeeded"]), evidence=result["evidence"],
                measure_before=frontier_now, measure_after=frontier_now,
                escalation_level="curiosity")

        self.db.stage_curiosity_learning(
            run_id=run_id,
            insight=result["insight"],
            confidence=float(result["confidence"]),
            evidence=result["evidence"],
            outcome=result["outcome"],
            applies_when=result["applies_when"],
            falsified_by=result["falsified_by"],
        )
        log.info("curiosity cycle complete — run #%s staged inert proposal", run_id)

    # -- run forever --------------------------------------------------------
    def forever(self, interval_s: int = 300) -> None:
        """The system's actual life. Turn, wait, turn again.

        A rate limit is not a crash — it's a subscription doing its job. We wait it out and pick
        up exactly where we left off.
        """
        # A restart must NOT wash away an unresolved hibernation. If we come back up stuck, we
        # go straight back to waiting — without re-emailing, and without spending a cycle to
        # rediscover that we are stuck.
        open_h = self.db.open_hibernation()
        if open_h is not None:
            log.error("came up with an UNRESOLVED hibernation (#%s) — waiting for a real signal, "
                      "not spending.", open_h["id"])
            self.enter_hibernation(Hibernate(open_h["reason"]))

        while True:
            try:
                self.cycle()
            except RateLimited as e:
                wait = e.retry_after_s or 900
                # DEADLINE, DB-computed. After it passes, "rate limited" stops being an excuse.
                self.db.beat("rate_limited", f"plan exhausted; waiting {wait}s", retry_after_s=wait)
                log.warning("rate limited — sleeping %ss. The loop is fine; the plan is busy.", wait)
                time.sleep(wait)
                continue
            except Hibernate as e:
                # Stuck, everything tried. STOP spending — but DO NOT vanish.
                #
                # The bug this replaces: we exited, systemd restarted us (Restart=always), we
                # re-read the same streak, hibernated again and EMAILED AGAIN — forever. Cost
                # control became a notify storm, and nothing recorded that a human was needed,
                # so their reply had nothing to resume.
                self.enter_hibernation(e)
                return
            except BudgetExceeded as e:
                log.error("HARD CAP REACHED — the loop has STOPPED. %s", e)
                self.inst.surfaces.notify(
                    subject="[autonomy-quest] budget cap reached — the loop has stopped",
                    body=str(e),
                )
                return
            except AgentFailed as e:
                log.error("agent failed this cycle (recorded): %s", e)
            except Exception:
                log.exception("cycle blew up (recorded). Continuing — one bad cycle is not a dead loop.")
            time.sleep(interval_s)

    # -- 1. observe ---------------------------------------------------------
    def observe(self) -> dict:
        """Read the world. GROUND TRUTH ONLY.

        The mission's measure is re-read from wherever it actually lives — the query the human
        gave us in the interview. We do not trust a cached number, and we never ask the model how
        it thinks things are going. If the source is unreadable we stop: a loop steering on a
        number it did not actually read is worse than a halted loop, because it looks like it's
        working.
        """
        m = self.inst.mission.measure
        value = self.db.read_measure(m)   # raises if unreadable
        self.db.record_measurement(m, value)

        # RESOLVE THE TARGET LIVE. A frozen target drifts from a changing catalog; a target_query
        # is re-read from ground truth every cycle, so "the whole catalog kept fresh" stays true
        # as the catalog grows or shrinks.
        target = self.db.read_scalar(m.target_query) if m.target_query else (
            Decimal(str(m.target)) if m.target is not None else None)

        return {
            "mission": self.inst.mission,
            "now": value,
            "target": target,
            "satisfied": m.satisfied(value, target),
            "overshooting": m.overshooting(value, target),
            "trend": self.db.measure_trend(self.inst.mission.measure, days=14),
            "open_work": self.db.open_work(),
            "recent_runs": self.db.recent_runs(limit=10),
            # Everything it believes that hasn't been superseded. THIS is what makes cycle 100
            # different from cycle 1.
            "learnings": self.db.live_learnings(limit=50),
            "parked": self.db.awaiting_human(),
            "spent_today": self.budget.spent_today(),
        }

    # -- the gate -----------------------------------------------------------
    def requires_human(self, work: Work) -> bool:
        level = self.inst.budget.autonomy.level
        if level == "propose":
            return True
        if work.requires_human:
            return True
        if level == "act-reversible":
            return not work.reversible or work.spends_money or work.touches_human or work.commits
        if level == "act-external":
            return work.spends_money or work.commits
        if level == "act-broad":
            return not self.inst.mission.within_boundaries(work)
        raise ValueError(f"unknown autonomy level: {level!r}")

    def reconcile_orphaned_runs(self) -> None:
        """A run that started and never recorded an outcome = the process was hard-killed mid-cycle.
        Mark it terminal (we do NOT know if the act had side effects, so say so) and reset its work
        to pending. Honest, not silent: the run stays in history as an interrupted failure, and the
        next cycle re-does the work fresh rather than blocking on a zombie."""
        for r in (self.db.orphaned_runs() or []):
            log.warning("run #%s was orphaned (started, never recorded) — a hard kill mid-cycle. "
                        "Marking interrupted; side effects UNKNOWN.", r["id"])
            self.db.fail_run(
                r["id"],
                "ORPHANED: the process exited mid-cycle before recording an outcome (hard kill: "
                "Ctrl-C / terminal close / reboot / OOM). The act may or may not have run — side "
                "effects UNKNOWN; verify before trusting. Work reset to pending.")

    def finish_pending_reflection(self) -> None:
        """Complete a cycle that acted but never learned.

        The learning is the thing that makes this a loop and not a cron job, and a run stuck
        without one is a hole in the history — verify.sh will (correctly) refuse to count it, and
        the loop would otherwise redo work that was already done.
        """
        p = self.db.pending_reflection()
        if not p:
            return
        log.warning("run #%s acted but never learned (rate limit or crash) — FINISHING it, not redoing it",
                    p["id"])
        work = Work(id=p["work_id"], kind="", summary=p["summary"], rationale=p["rationale"])
        insight, _ = self.ex.run(
            prompts.reflect(work, p["outcome"], p["succeeded"], self.db.live_learnings(limit=50)),
            prompts.REFLECT_SCHEMA, tier="reasoning")
        measure_now = self.db.read_measure(self.inst.mission.measure)
        with self.db.tx() as tx:
            self.db.complete_run(
                tx, p["id"], outcome=p["outcome"], succeeded=p["succeeded"], usage=Usage(),
                productive=self.esc.was_productive(
                    p["outcome"], p["succeeded"], p["evidence"] or "", measure_now, measure_now),
                evidence=p["evidence"] or "", measure_before=measure_now, measure_after=measure_now)
            lid = self.db.write_learning(
                tx, run_id=p["id"], insight=insight["insight"], evidence=insight["evidence"],
                scope=insight["scope"], confidence=insight["confidence"])
            self.db.graph_link(tx, run_id=p["id"], work_id=p["work_id"], learning_id=lid)
        log.info("run #%s finished — the cycle is whole again", p["id"])

    # -- hibernation ---------------------------------------------------------
    def enter_hibernation(self, e: Hibernate) -> None:
        """Record it, tell the human ONCE, and WAIT for a real signal.

        We do not exit the process. Exiting hands control to the supervisor, which will restart
        us into the same wall. We sit here, cheaply, spending nothing, until a human approves
        something or a peer sends a message.
        """
        open_h = self.db.open_hibernation()
        if open_h is None:
            hid = self.db.hibernate(str(e), self.esc.unproductive_streak())
            open_h = self.db.open_hibernation()
            log.error("HIBERNATING (#%s). %s", hid, e)
        else:
            log.error("still hibernating (#%s) — NOT re-notifying.", open_h["id"])

        if open_h["notified_at"] is None:
            self.inst.surfaces.notify(
                subject=f"[autonomy-quest] {self.inst.engine.resident_agent} instance HIBERNATED — it is stuck and has stopped",
                body=(f"{e}\n\nMISSION: {self.inst.mission.objective}\n\n"
                      f"It has STOPPED SPENDING. It will resume the moment you approve something "
                      f"in the UI, or an agent sends it a message — and not before. Elapsed time "
                      f"will not wake it, and neither will a reboot."))
            self.db.mark_hibernation_notified(open_h["id"])

        # Wait for a REAL signal. Not a timer.
        #
        # NON-BUSY and SUPERVISOR-COMPATIBLE: we sleep, we make ZERO model calls, and we emit a
        # cheap heartbeat so a watchdog can tell "correctly waiting" from "dead" — WITHOUT it
        # counting as progress. A hibernating loop that looks dead gets killed and restarted into
        # the same wall; a hibernating loop that looks productive resets the ladder. It must look
        # like exactly what it is: alive, stopped, and waiting for you.
        while True:
            self.db.beat("hibernating", f"waiting on a human or peer signal (#{open_h['id']})")
            sig = self.db.resume_signal(open_h["hibernated_at"])
            if sig:
                self.db.resume_hibernation(open_h["id"], sig)
                log.warning("RESUMING — %s", sig)
                self.db.beat("turning", f"resumed: {sig[:80]}")
                return
            time.sleep(30)

    def notify_human_stuck(self, verdict, world) -> None:
        """The loop is stuck enough to be worth a human's attention. Say what was tried."""
        recent = "\n".join(f"- {r['summary']}: {r['outcome'][:120]}" for r in world["recent_runs"][:5])
        self.inst.surfaces.notify(
            subject=f"[autonomy-quest] stuck — {verdict.unproductive} cycles produced nothing",
            body=(f"The loop has produced no artifact and moved no number for "
                  f"{verdict.unproductive} consecutive cycles.\n\n"
                  f"MISSION: {self.inst.mission.objective}\n"
                  f"MEASURE: {self.inst.mission.measure.what} = {world['now']}\n\n"
                  f"WHAT IT TRIED:\n{recent}\n\n"
                  f"It will keep trying different angles, and will HIBERNATE (stop spending) "
                  f"at {12} consecutive unproductive cycles rather than burn your budget on a wall."))

    def notify_human(self, work: Work, reason: str | None = None) -> None:
        """Reach them where they said they'd be (interview/06-surfaces.md).

        If this fails we FAIL LOUD. A parked decision nobody was told about is a stalled loop that
        looks healthy — the most common way one of these quietly dies.

        `reason` (when set, e.g. a consult-act deferral note) is surfaced FIRST so the human sees
        WHY the loop gated this — a consult-act park is not indistinguishable from a base-gate park.
        """
        body = f"{work.rationale}\n\nApprove it and the loop will pick it up on the next cycle."
        if reason:
            body = f"{reason}\n\n{body}"
        self.inst.surfaces.notify(
            subject=f"[autonomy-quest] needs your decision: {work.summary}",
            body=body,
        )
