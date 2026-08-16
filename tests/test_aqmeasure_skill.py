"""Functional tests for the seeded agent's READ-ONLY `aqmeasure` tool (W1b, task #6087).

The script is an executable named `aqmeasure` (no .py suffix), loaded by path like `aqdb`. The DB is
faked with a recording connection/cursor so we can assert the fail-closed freshness verdicts AND the
security-relevant invariants: the session is read-only, there is NO write command, and the tool
never issues anything but SELECTs.
"""

import datetime as dt
import importlib.machinery
import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "agent_skills" / "db" / "aqmeasure"


def _load():
    spec = importlib.util.spec_from_file_location(
        "aqmeasure_mod", str(_PATH),
        loader=importlib.machinery.SourceFileLoader("aqmeasure_mod", str(_PATH)),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aqmeasure = _load()


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.calls.append((sql, params))
        self._last = sql

    def fetchone(self):
        sql = self._last
        if "f(latest)" in sql or ("max(occurred_at)" in sql and "fresh" in sql):
            return (self.conn.latest, self.conn.fresh)
        if "count(*)" in sql:
            return (self.conn.count,)
        return None

    def fetchall(self):
        return list(self.conn.rows)

    @property
    def description(self):
        return None


class FakeConnection:
    latest = None
    fresh = False
    count = 0
    rows = []

    def __init__(self):
        self.calls = []
        self.readonly = None

    def set_session(self, readonly=False, autocommit=False):
        self.readonly = readonly

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


@pytest.fixture
def fake_db(monkeypatch):
    holder = {}

    def _connect(dsn):
        conn = FakeConnection()
        holder["conn"] = conn
        return conn

    monkeypatch.setenv("AQ_DB_URL", "postgres://aq_actor@localhost/aq")
    monkeypatch.setattr(aqmeasure.psycopg2, "connect", _connect)
    return holder


def test_missing_dsn_fails_clearly(monkeypatch, capsys):
    monkeypatch.delenv("AQ_DB_URL", raising=False)
    rc = aqmeasure.main(["current", "--event-type", "signup"])
    assert rc == 2
    assert "AQ_DB_URL is not set" in capsys.readouterr().err


def test_session_is_read_only(fake_db):
    FakeConnection.latest = dt.datetime.now(dt.timezone.utc)
    FakeConnection.fresh = True
    FakeConnection.count = 5
    aqmeasure.main(["current", "--event-type", "signup"])
    assert fake_db["conn"].readonly is True


def test_current_fresh_reports_advisory_value(fake_db, capsys):
    FakeConnection.latest = dt.datetime.now(dt.timezone.utc)
    FakeConnection.fresh = True
    FakeConnection.count = 12
    rc = aqmeasure.main(["current", "--event-type", "signup", "--json"])
    assert rc == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "fresh"
    assert payload["advisory_value"] == 12
    assert payload["authority"].startswith("advisory_only")


def test_current_empty_is_unknown_not_zero(fake_db, capsys):
    FakeConnection.latest = None
    FakeConnection.fresh = False
    FakeConnection.count = 0
    rc = aqmeasure.main(["current", "--event-type", "signup", "--json"])
    assert rc == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "unknown"
    assert payload["advisory_value"] is None


def test_current_stale_is_unknown(fake_db, capsys):
    FakeConnection.latest = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)
    FakeConnection.fresh = False
    FakeConnection.count = 99
    rc = aqmeasure.main(["current", "--event-type", "signup", "--json"])
    assert rc == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "stale"
    assert payload["advisory_value"] is None


def test_window_is_bound_parameter(fake_db):
    FakeConnection.latest = dt.datetime.now(dt.timezone.utc)
    FakeConnection.fresh = True
    FakeConnection.count = 1
    aqmeasure.main(["current", "--event-type", "signup", "--window", "7 days"])
    fresh_call = next(c for c in fake_db["conn"].calls if "fresh" in c[0])
    assert fresh_call[1] == ("7 days",)
    assert "7 days" not in fresh_call[0]


def test_no_write_command_exists():
    """The tool exposes only read subcommands — no add/insert/write path at all."""
    parser = aqmeasure.build_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None))
    assert set(sub.choices) == {"current", "recent"}


def test_only_selects_are_issued(fake_db):
    FakeConnection.latest = dt.datetime.now(dt.timezone.utc)
    FakeConnection.fresh = True
    FakeConnection.count = 3
    aqmeasure.main(["current", "--event-type", "signup"])
    for sql, _ in fake_db["conn"].calls:
        head = sql.strip().split()[0].upper()
        assert head in {"SELECT", "SET"}, sql
