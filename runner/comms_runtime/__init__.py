"""Intra-instance multi-agent runtime (#4834 comms Phase 4, design §6/§8/§10).

This package is INERT by default. Nothing here is imported or constructed unless a workflow
declares role agents AND ``AQ_WORKFLOW_MULTI_AGENT`` is set (see ``runner.role_config``). It grants
NO host / docker / replication capability: it is a durable intra-instance mailbox bus, a bounded
role scheduler, a wake-delivery interface (subprocess default, optional tmux adapter), and scoped
per-role principals. The loop-owned gates stay in ``runner.loop.Loop``, downstream of every role
conversation — consensus is not authority.
"""
