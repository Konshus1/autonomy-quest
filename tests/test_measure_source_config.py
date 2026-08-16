"""W1b measure bridge — config parsing + LOAD-time validation (task #6087).

A stock instance (no `source:`) must parse to the default business-DB measure UNCHANGED. A
non-default source must be a known adapter, and an external source must carry a freshness_query —
both validated at load so a typo fails LOUD when committed, not silently weeks later.
"""

from __future__ import annotations

import textwrap

import pytest

from runner.config import Instance, Unaimed
from runner import measure_source as ms


_BASE = """\
template: running-a-business
datastore:
  graph: none
mission:
  objective: "Grow the thing"
  measure:
{measure_body}
  horizon: "weekly"
  boundaries:
    may_act_alone: []
    must_ask_first: []
"""


def _write(tmp_path, measure_body: str):
    path = tmp_path / "instance.yaml"
    path.write_text(_BASE.format(measure_body=textwrap.indent(textwrap.dedent(measure_body), "    ")))
    return str(path)


def test_default_measure_has_business_db_source(tmp_path):
    path = _write(tmp_path, """\
        what: "active paying customers"
        where: "select count(*) from subscriptions where status='active'"
    """)
    inst = Instance.load(path)
    m = inst.mission.measure
    assert m.source == ms.BUSINESS_DB_SOURCE
    assert m.freshness_query is None
    assert m.freshness_window == "24 hours"
    # And it resolves to the byte-identical business path (no env flag set).
    assert ms.effective_measure_source(m) == ms.BUSINESS_DB_SOURCE


def test_analytics_source_parses(tmp_path):
    path = _write(tmp_path, """\
        what: "signups"
        where: "select count(*) from analytics_events where event_type='signup'"
        source: analytics_events
        freshness_query: "select max(occurred_at) from analytics_events"
        freshness_window: "12 hours"
    """)
    inst = Instance.load(path)
    m = inst.mission.measure
    assert m.source == ms.ANALYTICS_EVENTS_SOURCE
    assert m.freshness_query == "select max(occurred_at) from analytics_events"
    assert m.freshness_window == "12 hours"


def test_unknown_source_fails_loud(tmp_path):
    path = _write(tmp_path, """\
        what: "signups"
        where: "select 1"
        source: ga4_but_unregistered
        freshness_query: "select max(occurred_at) from analytics_events"
    """)
    with pytest.raises(Unaimed, match="not a known adapter"):
        Instance.load(path)


def test_external_source_without_freshness_fails_loud(tmp_path):
    path = _write(tmp_path, """\
        what: "signups"
        where: "select count(*) from analytics_events"
        source: analytics_events
    """)
    with pytest.raises(Unaimed, match="freshness_query"):
        Instance.load(path)
