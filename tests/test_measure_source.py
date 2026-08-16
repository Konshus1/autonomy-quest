"""W1b real-world measure bridge — the pluggable, fail-closed measure SOURCE (task #6087).

These are pure-Python unit tests (no live Postgres): the adapter and `Db.read_measure`'s dispatch
are exercised through a fake `run_query`, so the anti-Goodhart + fail-closed behaviour is pinned
deterministically.

The properties under test:
  * flag OFF (default source) => the read is the historical business-DB path, UNCHANGED.
  * the value ALWAYS comes from the source read — never from anything a plan could claim.
  * fresh analytics => the real count; empty / stale / unreachable => FAIL CLOSED (MeasureUnreadable),
    NO fabricated number, and the value query is not even reached when liveness fails.
  * an unknown source name fails closed instead of silently reading the business DB.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from runner import measure_source as ms
from runner.config import Measure
from runner.db import Db, MeasureUnreadable


# ---------------------------------------------------------------------------
# A fake host-side querier standing in for Db._q(sql, args, one=...).
# ---------------------------------------------------------------------------
class FakeQuerier:
    def __init__(self, *, freshness_row=None, value_row=None, raise_on=None):
        self.freshness_row = freshness_row
        self.value_row = value_row
        self.raise_on = raise_on          # 'freshness' | 'value' | 'all' | None
        self.calls = []

    def q(self, sql, args=(), one=False):
        self.calls.append((sql, args, one))
        is_freshness = "f(latest)" in sql
        if self.raise_on == "all" or (self.raise_on == "freshness" and is_freshness) \
                or (self.raise_on == "value" and not is_freshness):
            raise RuntimeError("boom: source unreachable")
        return self.freshness_row if is_freshness else self.value_row

    @property
    def value_was_read(self):
        return any("f(latest)" not in sql for sql, _, _ in self.calls)


def _read(measure, querier):
    """Drive the real Db.read_measure dispatch without a DB connection."""
    fake_self = type("S", (), {"_q": staticmethod(querier.q)})()
    return Db.read_measure(fake_self, measure)


def _analytics(**kw):
    base = dict(
        what="signups", where="SELECT count(*) FROM analytics_events WHERE event_type='signup'",
        source=ms.ANALYTICS_EVENTS_SOURCE,
        freshness_query="SELECT max(occurred_at) FROM analytics_events",
        freshness_window="24 hours",
    )
    base.update(kw)
    return Measure(**base)


# ---------------------------------------------------------------------------
# effective source resolution (env override > config > default)
# ---------------------------------------------------------------------------
def test_default_source_is_business_db(monkeypatch):
    monkeypatch.delenv(ms.MEASURE_SOURCE_ENV, raising=False)
    m = Measure(what="c", where="select 1")
    assert ms.effective_measure_source(m) == ms.BUSINESS_DB_SOURCE


def test_config_source_selects_adapter(monkeypatch):
    monkeypatch.delenv(ms.MEASURE_SOURCE_ENV, raising=False)
    assert ms.effective_measure_source(_analytics()) == ms.ANALYTICS_EVENTS_SOURCE


def test_env_flag_overrides_config(monkeypatch):
    monkeypatch.setenv(ms.MEASURE_SOURCE_ENV, ms.ANALYTICS_EVENTS_SOURCE)
    m = Measure(what="c", where="select 1")   # config says business_db
    assert ms.effective_measure_source(m) == ms.ANALYTICS_EVENTS_SOURCE


def test_blank_env_flag_is_inert(monkeypatch):
    monkeypatch.setenv(ms.MEASURE_SOURCE_ENV, "   ")
    m = Measure(what="c", where="select 1")
    assert ms.effective_measure_source(m) == ms.BUSINESS_DB_SOURCE


# ---------------------------------------------------------------------------
# flag OFF => business-DB path is byte-identical (dispatch just runs `where`).
# ---------------------------------------------------------------------------
def test_business_db_path_reads_where_query(monkeypatch):
    monkeypatch.delenv(ms.MEASURE_SOURCE_ENV, raising=False)
    q = FakeQuerier(value_row={"count": 7})
    m = Measure(what="paying", where="SELECT count(*) FROM subscriptions")
    assert _read(m, q) == Decimal("7")
    # Exactly one query, the mission's own `where`, no freshness machinery.
    assert len(q.calls) == 1
    assert q.calls[0][0] == "SELECT count(*) FROM subscriptions"


def test_business_db_unreadable_still_raises(monkeypatch):
    monkeypatch.delenv(ms.MEASURE_SOURCE_ENV, raising=False)
    q = FakeQuerier(raise_on="all")
    m = Measure(what="paying", where="SELECT count(*) FROM subscriptions")
    with pytest.raises(MeasureUnreadable):
        _read(m, q)


# ---------------------------------------------------------------------------
# analytics adapter — the real-world read.
# ---------------------------------------------------------------------------
def test_analytics_fresh_returns_real_count(monkeypatch):
    monkeypatch.delenv(ms.MEASURE_SOURCE_ENV, raising=False)
    now = dt.datetime.now(dt.timezone.utc)
    q = FakeQuerier(freshness_row={"latest": now, "fresh": True}, value_row={"count": 42})
    assert _read(_analytics(), q) == Decimal("42")


def test_analytics_value_comes_from_source_not_a_claim(monkeypatch):
    """Anti-Goodhart: whatever a plan might 'claim', the returned number is the DB read."""
    monkeypatch.delenv(ms.MEASURE_SOURCE_ENV, raising=False)
    now = dt.datetime.now(dt.timezone.utc)
    q = FakeQuerier(freshness_row={"latest": now, "fresh": True}, value_row={"count": 3})
    m = _analytics()
    m.claimed_value = 9999          # a convenient number attached anywhere
    assert _read(m, q) == Decimal("3")


def test_analytics_empty_fails_closed_and_skips_value(monkeypatch):
    monkeypatch.delenv(ms.MEASURE_SOURCE_ENV, raising=False)
    q = FakeQuerier(freshness_row={"latest": None, "fresh": False}, value_row={"count": 5})
    with pytest.raises(MeasureUnreadable, match="NO events"):
        _read(_analytics(), q)
    assert not q.value_was_read          # never credited a convenient zero/value


def test_analytics_stale_fails_closed(monkeypatch):
    monkeypatch.delenv(ms.MEASURE_SOURCE_ENV, raising=False)
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)
    q = FakeQuerier(freshness_row={"latest": old, "fresh": False}, value_row={"count": 5})
    with pytest.raises(MeasureUnreadable, match="STALE"):
        _read(_analytics(), q)
    assert not q.value_was_read


def test_analytics_unreachable_fails_closed(monkeypatch):
    monkeypatch.delenv(ms.MEASURE_SOURCE_ENV, raising=False)
    q = FakeQuerier(raise_on="freshness")
    with pytest.raises(MeasureUnreadable, match="freshness"):
        _read(_analytics(), q)


def test_analytics_missing_freshness_query_fails_closed(monkeypatch):
    monkeypatch.delenv(ms.MEASURE_SOURCE_ENV, raising=False)
    q = FakeQuerier(value_row={"count": 5})
    with pytest.raises(MeasureUnreadable, match="freshness_query"):
        _read(_analytics(freshness_query=None), q)


def test_unknown_source_fails_closed_not_business_db(monkeypatch):
    monkeypatch.setenv(ms.MEASURE_SOURCE_ENV, "plausible_but_unregistered")
    q = FakeQuerier(value_row={"count": 5})
    with pytest.raises(MeasureUnreadable, match="not a known adapter"):
        _read(Measure(what="c", where="SELECT 1"), q)
    # Crucially it did NOT silently read the business-DB `where`.
    assert q.calls == []


def test_freshness_window_is_bound_parameter(monkeypatch):
    """The window crosses into SQL as a bound param, never string-formatted."""
    monkeypatch.delenv(ms.MEASURE_SOURCE_ENV, raising=False)
    now = dt.datetime.now(dt.timezone.utc)
    q = FakeQuerier(freshness_row={"latest": now, "fresh": True}, value_row={"count": 1})
    _read(_analytics(freshness_window="7 days"), q)
    fresh_call = next(c for c in q.calls if "f(latest)" in c[0])
    assert fresh_call[1] == ("7 days",)      # args tuple, parameterized
    assert "7 days" not in fresh_call[0]     # not interpolated into the SQL text
