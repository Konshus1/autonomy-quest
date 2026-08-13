"""Functional tests for the seeded agent's `aqdb` DB bridge (#4834).

The script is an executable named `aqdb` (no .py suffix), so it is loaded by path. The database
is faked with a recording connection/cursor: this lets us assert BOTH behaviour (measure returns
the count; add-* insert) AND the security-relevant invariants (parameterized SQL; the read-only
`query` guard rejects writes; missing AQ_DB_URL fails cleanly).
"""

import importlib.util
import os
import pathlib

import pytest

_AQDB_PATH = pathlib.Path(__file__).resolve().parents[1] / "agent_skills" / "db" / "aqdb"


def _load_aqdb():
    spec = importlib.util.spec_from_loader(
        "aqdb_mod", loader=None
    )
    # spec_from_file_location refuses the extensionless name for some loaders; load source directly.
    spec = importlib.util.spec_from_file_location(
        "aqdb_mod", str(_AQDB_PATH),
        loader=importlib.machinery.SourceFileLoader("aqdb_mod", str(_AQDB_PATH)),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aqdb = _load_aqdb()


# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------
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
        if "count(DISTINCT customer_id)" in sql:
            return (self.conn.measure_value,)
        if "FROM customers WHERE id" in sql:
            return (1,) if self.conn.customer_exists else None
        if "RETURNING id" in sql:
            return (self.conn.new_sub_id,)
        return None

    def fetchall(self):
        return list(self.conn.rows)

    @property
    def description(self):
        return self.conn.description

    @property
    def rowcount(self):
        return self.conn.rowcount


class FakeConnection:
    measure_value = 7
    customer_exists = True
    new_sub_id = 42
    rows = []
    description = None
    rowcount = 1

    def __init__(self):
        self.calls = []
        self.closed = False
        self.readonly = None

    def set_session(self, readonly=False, autocommit=False):
        self.readonly = readonly

    def cursor(self):
        return FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        self.closed = True


@pytest.fixture
def fake_db(monkeypatch):
    """Install a fake psycopg2.connect and return the connection instance used."""
    holder = {}

    def _connect(dsn):
        holder["dsn"] = dsn
        conn = FakeConnection()
        holder["conn"] = conn
        return conn

    monkeypatch.setenv("AQ_DB_URL", "postgres://aq_actor@localhost/aq")
    monkeypatch.setattr(aqdb.psycopg2, "connect", _connect)
    return holder


# ---------------------------------------------------------------------------
# Missing AQ_DB_URL
# ---------------------------------------------------------------------------
def test_missing_dsn_fails_clearly(monkeypatch, capsys):
    monkeypatch.delenv("AQ_DB_URL", raising=False)
    rc = aqdb.main(["measure"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "AQ_DB_URL is not set" in err


def test_blank_dsn_fails(monkeypatch):
    monkeypatch.setenv("AQ_DB_URL", "   ")
    with pytest.raises(aqdb.AqdbError):
        aqdb.get_dsn()


# ---------------------------------------------------------------------------
# measure
# ---------------------------------------------------------------------------
def test_measure_returns_count(fake_db, capsys):
    FakeConnection.measure_value = 20
    rc = aqdb.main(["measure", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"value": 20' in out
    sql, params = fake_db["conn"].calls[-1]
    assert "count(DISTINCT customer_id)" in sql
    assert "status = %s" in sql and params == ("active",)  # parameterized, not inlined
    assert fake_db["conn"].readonly is True


# ---------------------------------------------------------------------------
# add-customer
# ---------------------------------------------------------------------------
def test_add_customer_parameterized_insert(fake_db, capsys):
    FakeConnection.rowcount = 1
    rc = aqdb.main([
        "add-customer", "--id", "acme", "--name", "Acme Co",
        "--email", "ops@acme.example", "--json",
    ])
    assert rc == 0
    sql, params = fake_db["conn"].calls[-1]
    assert "INSERT INTO customers" in sql
    assert "ON CONFLICT (id) DO NOTHING" in sql
    # every value bound as a parameter — none of them appear inlined in the SQL text
    assert "acme" not in sql and "Acme Co" not in sql and "ops@acme.example" not in sql
    assert params == ("acme", "Acme Co", "ops@acme.example", "{}")
    assert '"inserted": true' in capsys.readouterr().out


def test_add_customer_conflict_reports_no_change(fake_db, capsys):
    FakeConnection.rowcount = 0
    rc = aqdb.main([
        "add-customer", "--id", "acme", "--name", "Acme", "--email", "a@b.c",
    ])
    assert rc == 0
    assert "already existed" in capsys.readouterr().out


def test_add_customer_bad_metadata_rejected(fake_db):
    rc = aqdb.main([
        "add-customer", "--id", "x", "--name", "n", "--email", "e@x.y",
        "--metadata", "not-json",
    ])
    assert rc == 2


def test_add_customer_metadata_must_be_object(fake_db):
    rc = aqdb.main([
        "add-customer", "--id", "x", "--name", "n", "--email", "e@x.y",
        "--metadata", "[1,2,3]",
    ])
    assert rc == 2


# ---------------------------------------------------------------------------
# add-subscription
# ---------------------------------------------------------------------------
def test_add_subscription_moves_measure(fake_db, capsys):
    FakeConnection.customer_exists = True
    FakeConnection.new_sub_id = 99
    rc = aqdb.main([
        "add-subscription", "--customer-id", "acme",
        "--status", "active", "--monthly-amount", "49", "--source", "research", "--json",
    ])
    assert rc == 0
    calls = [c for c in fake_db["conn"].calls if not c[0].startswith("SET ")]
    # existence check first, then a parameterized INSERT ... RETURNING id
    assert "FROM customers WHERE id = %s" in calls[0][0] and calls[0][1] == ("acme",)
    ins_sql, ins_params = calls[-1]
    assert "INSERT INTO subscriptions" in ins_sql and "RETURNING id" in ins_sql
    assert ins_params == ("acme", "active", 49.0, "research")
    assert "acme" not in ins_sql  # parameterized
    assert '"subscription_id": 99' in capsys.readouterr().out


def test_add_subscription_unknown_customer_fails(fake_db, capsys):
    FakeConnection.customer_exists = False
    rc = aqdb.main(["add-subscription", "--customer-id", "ghost"])
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err
    # no INSERT was attempted
    assert all("INSERT INTO subscriptions" not in c[0] for c in fake_db["conn"].calls)


def test_add_subscription_rejects_invalid_status(fake_db):
    with pytest.raises(SystemExit):  # argparse choices rejection
        aqdb.main(["add-subscription", "--customer-id", "acme", "--status", "bogus"])


# ---------------------------------------------------------------------------
# list-customers / query (reads)
# ---------------------------------------------------------------------------
def test_list_customers_readonly_and_limited(fake_db, capsys):
    FakeConnection.rows = [("acme", "Acme", "a@b.c", "2026-01-01")]
    rc = aqdb.main(["list-customers", "--limit", "5", "--json"])
    assert rc == 0
    sql, params = fake_db["conn"].calls[-1]
    assert "LIMIT %s" in sql and params == (5,)
    assert fake_db["conn"].readonly is True
    assert "acme" in capsys.readouterr().out


def test_query_runs_select(fake_db, capsys):
    FakeConnection.rows = [(20,)]
    FakeConnection.description = [("n",)]
    rc = aqdb.main(["query", "SELECT count(*) AS n FROM subscriptions", "--json"])
    assert rc == 0
    assert fake_db["conn"].readonly is True
    assert '"n": 20' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Read-only query guard — the core safety property (fail closed)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("good", [
    "SELECT 1",
    "select * from customers",
    "  WITH x AS (SELECT 1) SELECT * FROM x  ",
    "SELECT count(*) FROM subscriptions WHERE status='active';",  # single trailing ; ok
])
def test_query_guard_accepts_reads(good):
    # returns normalized statement without raising
    assert aqdb.validate_read_only_query(good)


@pytest.mark.parametrize("bad", [
    "INSERT INTO customers VALUES ('x','y','z')",
    "UPDATE subscriptions SET status='active'",
    "DELETE FROM customers",
    "DROP TABLE customers",
    "TRUNCATE customers",
    "ALTER TABLE customers ADD COLUMN x int",
    "CREATE TABLE t (id int)",
    "GRANT ALL ON customers TO aq_actor",
    "SELECT 1; DROP TABLE customers",          # ;-chaining
    "SELECT 1; SELECT 2",                       # ;-chaining, both selects
    "SELECT * FROM customers -- comment",      # comment smuggling
    "SELECT * FROM customers /* x */",         # block comment
    "SELECT * INTO newtbl FROM customers",     # SELECT ... INTO writes
    "WITH t AS (DELETE FROM customers RETURNING *) SELECT * FROM t",  # writable CTE
    "",
    "   ",
    "VACUUM",
    "COPY customers FROM '/etc/passwd'",
])
def test_query_guard_rejects_writes_and_chaining(bad):
    with pytest.raises(aqdb.AqdbError):
        aqdb.validate_read_only_query(bad)


def test_query_command_rejects_write_end_to_end(fake_db):
    rc = aqdb.main(["query", "DELETE FROM customers"])
    assert rc == 2
    # the guard fires BEFORE any connection/execute
    assert "conn" not in fake_db  # connect() was never called


# ---------------------------------------------------------------------------
# DB errors surface as clean messages, never raw tracebacks
# ---------------------------------------------------------------------------
def test_db_error_is_clean_not_traceback(fake_db, monkeypatch, capsys):
    """A DB-side error (bad table, permission denied, read-only rejection) must exit 2 with a
    clean `aqdb:` message — not escape main() as an uncaught psycopg2 traceback. The read-only
    session's rejection of a write function reaches the agent this way, so it must be legible."""
    def boom(self, sql, params=None):
        # emulate e.g. `permission denied` / `relation does not exist` / read-only rejection
        raise aqdb.psycopg2.Error("relation \"no_such_table\" does not exist")

    monkeypatch.setattr(FakeCursor, "execute", boom)
    rc = aqdb.main(["query", "SELECT * FROM no_such_table"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("aqdb:")
    assert "Traceback" not in err
