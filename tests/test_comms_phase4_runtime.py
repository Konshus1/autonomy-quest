"""Phase 4 intra-instance runtime mechanics (#4834 comms, design §6/§8/§10).

Covers: durable-first + cursor-poll recovery (dead pane / restart / missed push), role
impersonation rejected, session credential expiry, bounded fan-out, scheduler teardown (no orphans),
reviewer independence, and the planner/coder/reviewer happy path — all deterministic (subprocess/
tmux/docker are gated + skip loudly; none is required here).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runner.comms_runtime.mailbox_bus import MailboxBus, MailboxError
from runner.comms_runtime.principals import (
    Forbidden,
    RolePrincipalRegistry,
    Unauthenticated,
)
from runner.comms_runtime.scheduler import FanoutExceeded, RoleScheduler
from runner.comms_runtime.wake_delivery import SubprocessWakeDelivery
from runner.executor import Usage
from runner.role_config import RoleAgent, RoleConfig
from runner.comms_runtime.multi_agent_executor import MultiAgentExecutor


# --------------------------------------------------------------------------- #
# Durable-first mailbox + cursor recovery
# --------------------------------------------------------------------------- #

def test_message_is_durable_across_a_fresh_process_handle(tmp_path):
    """A stored message survives closing the bus and reopening the SAME db file (durable-first).

    This is the "an ephemeral store must never ack a message as durable" invariant: a brand-new
    MailboxBus handle on the same file re-reads the committed message.
    """
    db = tmp_path / "mb.sqlite3"
    bus = MailboxBus(db)
    seq = bus.publish(channel="run:r/role:coder/inbox", kind="role.code",
                      sender_role="planner", payload={"plan": "do X"})
    bus.close()

    reopened = MailboxBus(db)  # simulate a restarted / different delivery adapter process
    msgs, cursor = reopened.poll(channel="run:r/role:coder/inbox", cursor=0)
    assert [m.seq for m in msgs] == [seq]
    assert msgs[0].payload == {"plan": "do X"}
    assert cursor == seq


def test_missed_push_recovered_by_cursor_poll(tmp_path):
    """No wake is ever sent, yet the durable message is recovered by the next cursor poll.

    Delivery is best-effort on top of the store; losing every push loses nothing.
    """
    bus = MailboxBus(tmp_path / "mb.sqlite3")
    bus.publish(channel="c", kind="role.note", sender_role="coder", payload={"n": 1})
    bus.publish(channel="c", kind="role.note", sender_role="coder", payload={"n": 2})
    # Reader had cursor 0 and no notification fired; it polls anyway and gets both.
    msgs, cursor = bus.poll(channel="c", cursor=0)
    assert [m.payload["n"] for m in msgs] == [1, 2]
    # Polling again from the new cursor returns nothing (idempotent progress).
    again, cursor2 = bus.poll(channel="c", cursor=cursor)
    assert again == [] and cursor2 == cursor


def test_closed_schema_rejects_non_role_kinds(tmp_path):
    bus = MailboxBus(tmp_path / "mb.sqlite3")
    for bad in ("command", "shell", "exec", "run", "operator.message", "work.request"):
        with pytest.raises(MailboxError):
            bus.publish(channel="c", kind=bad, sender_role="coder", payload={})


# --------------------------------------------------------------------------- #
# Scoped per-role principals — impersonation + expiry
# --------------------------------------------------------------------------- #

def test_forged_or_absent_credential_authenticates_as_nobody():
    reg = RolePrincipalRegistry(instance_id="i", run_id="r")
    reg.mint("coder")
    with pytest.raises(Unauthenticated):
        reg.verify("not-a-real-token")
    with pytest.raises(Unauthenticated):
        reg.verify("")


def test_coder_cannot_address_parent_host_operator_or_other_run():
    reg = RolePrincipalRegistry(instance_id="i", run_id="r")
    tok, coder = reg.mint("coder")
    # Legitimate: hand off to the reviewer's inbox in the SAME run.
    reg.authorize_channel(coder, "run:r/role:reviewer/inbox")
    # Impersonation / privilege escalation attempts are all Forbidden — there is no parent/host/
    # operator channel in the role namespace, and another run is out of scope.
    for bad in ("run:r/role:reviewer/outbox", "instance:i/operator/inbox",
                "host/admin", "run:OTHER/role:coder/inbox", "operator.message"):
        with pytest.raises(Forbidden):
            reg.authorize_channel(coder, bad)


def test_naming_a_role_in_the_body_does_not_change_server_derived_identity():
    """The bus stamps the sender from the credential, never from a payload field claiming a role."""
    sched = RoleScheduler(instance_id="i", run_id="r", delivery=SubprocessWakeDelivery(),
                          fanout_cap=4)
    try:
        coder_tok = sched.start_role("coder")
        sched.start_role("reviewer")
        # Coder posts to reviewer, LYING that it is the reviewer in the payload.
        sched.dispatch(sender_token=coder_tok, to_role="reviewer", kind="role.code",
                       payload={"sender_role": "reviewer", "from": "reviewer", "decision": "x"})
        rev_tok = sched.token_for("reviewer")
        got = sched.receive(role_token=rev_tok)
        assert len(got) == 1
        assert got[0].sender_role == "coder"  # server-derived, not the body's lie
    finally:
        sched.teardown()


def test_session_credentials_expire():
    clock = {"t": 1000.0}
    reg = RolePrincipalRegistry(instance_id="i", run_id="r", session_ttl_s=30)
    reg._now = lambda: clock["t"]
    tok, _ = reg.mint("coder")
    assert reg.verify(tok).role == "coder"       # live
    clock["t"] += 31                              # past TTL
    with pytest.raises(Unauthenticated):
        reg.verify(tok)


def test_teardown_revokes_every_credential_no_orphans():
    reg = RolePrincipalRegistry(instance_id="i", run_id="r")
    t1, _ = reg.mint("coder")
    t2, _ = reg.mint("reviewer")
    assert reg.revoke_all() == 2
    for t in (t1, t2):
        with pytest.raises(Unauthenticated):
            reg.verify(t)


# --------------------------------------------------------------------------- #
# Bounded fan-out
# --------------------------------------------------------------------------- #

def test_fanout_is_bounded_extra_role_refused():
    sched = RoleScheduler(instance_id="i", run_id="r", delivery=SubprocessWakeDelivery(),
                          fanout_cap=2)
    try:
        sched.start_role("planner")
        sched.start_role("coder")
        with pytest.raises(FanoutExceeded):
            sched.start_role("reviewer")   # third live session over a cap of 2
    finally:
        sched.teardown()


def test_scheduler_teardown_closes_bus_and_reports_counts():
    sched = RoleScheduler(instance_id="i", run_id="r", delivery=SubprocessWakeDelivery(),
                          fanout_cap=4)
    sched.start_role("coder")
    sched.start_role("reviewer")
    result = sched.teardown()
    assert result == {"revoked": 2, "sessions": 2}
    # Idempotent: a second teardown is a no-op, not a crash.
    assert sched.teardown() == {"revoked": 0, "sessions": 0}


# --------------------------------------------------------------------------- #
# Reviewer independence
# --------------------------------------------------------------------------- #

def _decide_schema():
    return {
        "type": "object",
        "required": ["kind", "plan"],
        "properties": {"kind": {"type": "string"}, "plan": {"type": "object"}},
    }


def _scripted_factory(scripts):
    """scripts: {role: payload}. Each worker returns its scripted payload verbatim."""
    def factory(agent: RoleAgent):
        class _W:
            def run_turn(self, *, role, inbox, schema, tier):
                return scripts[role], Usage()
        return _W()
    return factory


def test_independent_reviewer_sharing_producer_config_is_refused():
    cfg = RoleConfig(agents={
        "coder": RoleAgent(role="coder", model="m", context=("plan",)),
        "reviewer": RoleAgent(role="reviewer", model="m", context=("plan",), independent=True),
    })
    with pytest.raises(ValueError):
        MultiAgentExecutor(base_executor=object(), role_config=cfg, instance_id="i")


def test_independent_reviewer_with_model_omitted_but_same_context_is_refused():
    """RED-FIRST (Finding 1): an omitted model INHERITS the producer's on the subscription path, so
    a reviewer that declares independence, omits model, and shares the producer's context is a
    correlated echo — it must be REJECTED at construction (was wrongly ACCEPTED before the fix)."""
    cfg = RoleConfig(agents={
        "coder": RoleAgent(role="coder", model="coder-m", context=("plan",)),
        "reviewer": RoleAgent(role="reviewer", model=None, context=("plan",), independent=True),
    })
    with pytest.raises(ValueError, match="effective model"):
        MultiAgentExecutor(base_executor=object(), role_config=cfg, instance_id="i")


def test_independent_reviewer_model_omitted_but_distinct_context_is_accepted():
    """An independent reviewer that omits model but has a genuinely DISTINCT context still
    constructs (distinct context is real differentiation)."""
    cfg = RoleConfig(agents={
        "coder": RoleAgent(role="coder", model="coder-m", context=("plan",)),
        "reviewer": RoleAgent(role="reviewer", model=None, context=("artifact",), independent=True),
    })
    ex = MultiAgentExecutor(base_executor=object(), role_config=cfg, instance_id="i")
    assert ex.reviewer_is_independent("reviewer") is True


def test_both_models_omitted_same_context_independent_is_refused():
    """Producer and reviewer both omit model (both inherit -> equal) and share context => refused."""
    cfg = RoleConfig(agents={
        "coder": RoleAgent(role="coder", model=None, context=("plan",)),
        "reviewer": RoleAgent(role="reviewer", model=None, context=("plan",), independent=True),
    })
    with pytest.raises(ValueError, match="effective model"):
        MultiAgentExecutor(base_executor=object(), role_config=cfg, instance_id="i")


def test_independent_reviewer_with_distinct_config_is_accepted_and_tagged():
    cfg = RoleConfig(agents={
        "coder": RoleAgent(role="coder", model="coder-m", context=("plan",)),
        "reviewer": RoleAgent(role="reviewer", model="rev-m", context=("artifact",),
                              independent=True),
    })
    ex = MultiAgentExecutor(base_executor=object(), role_config=cfg, instance_id="i",
                            delivery=SubprocessWakeDelivery(),
                            worker_factory=_scripted_factory({
                                "coder": {"decision": {"kind": "k", "plan": {}}},
                                "reviewer": {"verdict": "APPROVED"},
                            }))
    decision, _ = ex.run("prompt", _decide_schema(), tier="reasoning")
    assert decision == {"kind": "k", "plan": {}}
    assert ex.reviewer_is_independent("reviewer") is True
    assert ex.last_review_evidence == [
        {"role": "reviewer", "verdict": "APPROVED", "notes": None, "independent": True}]


# --------------------------------------------------------------------------- #
# Planner/coder/reviewer happy path — handoffs go through the DURABLE bus
# --------------------------------------------------------------------------- #

def test_planner_coder_reviewer_happy_path_uses_durable_handoffs():
    cfg = RoleConfig(agents={
        "planner": RoleAgent(role="planner", model="p"),
        "coder": RoleAgent(role="coder", model="c"),
        "reviewer": RoleAgent(role="reviewer", model="r"),
    })
    seen_inboxes: dict[str, list] = {}

    def factory(agent: RoleAgent):
        role = agent.role
        class _W:
            def run_turn(self, *, role, inbox, schema, tier):
                seen_inboxes[role] = inbox
                if role == "planner":
                    return {"plan_note": "start from mission"}, Usage()
                if role == "coder":
                    return {"decision": {"kind": "research", "plan": {"steps": []}}}, Usage()
                return {"verdict": "looks fine"}, Usage()
        return _W()

    ex = MultiAgentExecutor(base_executor=object(), role_config=cfg, instance_id="i",
                            delivery=SubprocessWakeDelivery(), worker_factory=factory)
    decision, _ = ex.run("prompt", _decide_schema(), tier="reasoning")
    assert decision == {"kind": "research", "plan": {"steps": []}}
    # Coder saw the planner's durable handoff; reviewer saw the coder's — proving the bus carried
    # each turn (cursor-polled from the durable inbox), not an in-memory shortcut.
    assert seen_inboxes["planner"] == []
    assert seen_inboxes["coder"][0]["sender_role"] == "planner"
    assert seen_inboxes["reviewer"][0]["sender_role"] == "coder"


# --------------------------------------------------------------------------- #
# Dead pane / restart with mailbox recovery
# --------------------------------------------------------------------------- #

def test_dead_pane_restart_recovers_durable_message_from_cursor(tmp_path):
    """A handoff is durably written; the recipient 'pane' dies WITHOUT ever polling; a fresh
    scheduler (restart) on the SAME run + db re-polls from cursor 0 and recovers the message.

    Proves loss of the delivery adapter loses nothing — the durable mailbox is the truth.
    """
    db = str(tmp_path / "mailbox.sqlite3")
    # Run instance A: planner hands off to the coder, then A is torn down (pane/process dies)
    a = RoleScheduler(instance_id="i", run_id="run-42", delivery=SubprocessWakeDelivery(),
                      fanout_cap=4, db_path=db)
    planner_tok = a.start_role("planner")
    a.start_role("coder")  # coder session exists but never gets to poll
    a.dispatch(sender_token=planner_tok, to_role="coder", kind="role.plan",
               payload={"plan": "durable across restart"})
    # Simulate a hard death: close the bus WITHOUT teardown-cleanup of the tmp file (external db_path
    # is not auto-removed), losing all in-memory delivery state.
    a._bus.close()

    # Run instance B: a fresh scheduler on the SAME db + run id. The coder re-polls from cursor 0.
    b = RoleScheduler(instance_id="i", run_id="run-42", delivery=SubprocessWakeDelivery(),
                      fanout_cap=4, db_path=db)
    try:
        b.start_role("coder")
        recovered = b.receive(role_token=b.token_for("coder"))
        assert len(recovered) == 1
        assert recovered[0].payload == {"plan": "durable across restart"}
        assert recovered[0].sender_role == "planner"
    finally:
        b.teardown()


# --------------------------------------------------------------------------- #
# tmux adapter is behind the interface and NOT in the default image (skip loudly)
# --------------------------------------------------------------------------- #

def test_tmux_delivery_is_optional_and_fails_loud_without_the_binary():
    """Selecting tmux delivery when no tmux binary is present raises loudly (the default image ships
    none). When tmux IS present, the adapter builds and a wake is a best-effort no-op nudge."""
    import shutil
    from runner.comms_runtime.wake_delivery import (
        TmuxUnavailable,
        TmuxWakeDelivery,
        build_wake_delivery,
    )
    if shutil.which("tmux") is None:
        with pytest.raises(TmuxUnavailable):
            build_wake_delivery("tmux")
        pytest.skip("tmux not installed (expected on the default image) — loud-skip after asserting "
                    "the selection fails closed")
    adapter = TmuxWakeDelivery(socket_name="aq-comms-test")
    adapter.register("sess-1", pane="no-such-pane")
    # A wake to a non-existent pane is best-effort and never raises; recovery is the cursor poll.
    assert isinstance(adapter.wake("sess-1"), bool)
    adapter.teardown("sess-1")
